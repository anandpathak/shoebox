import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { isStandalone } from '../components/InstallHint'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { detectPlatform } from '../lib/device'
import { ago, bytes } from '../lib/format'
import { local } from '../local/client'
import { enableOnDevice } from '../local/enable'
import { clearOfflineImages, downloadThumbs, savedCount } from '../local/offline'
import { dbName, downloadDeviceCatalog, removeDeviceCatalog, setOpen, type Published } from '../local/devices'
import { publishLocal } from '../local/publish'
import { refreshLocal } from '../local/refresh'
import { clearLocal, getLocal, onLocal, setLocal, type LocalState } from '../local/state'


export function Catalogs() {
  const { online, changed } = useApp()
  const [state, setState] = useState(getLocal)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [usage, setUsage] = useState<{ usage?: number; quota?: number }>({})
  const [available, setAvailable] = useState<boolean | null>(null)
  const [reason, setReason] = useState<string | null>(null)
  useEffect(() => onLocal(setState), [])
  useEffect(() => {
    navigator.storage?.estimate?.().then(setUsage).catch(() => {})
    local.call<{ persistent: boolean; reason: string | null }>('status')
      .then((s) => { setAvailable(s.persistent); setReason(s.reason) }).catch(() => setAvailable(false))
  }, [state])

  const enable = async () => {
    setError(null)
    setBusy('Downloading…')
    try {
      await enableOnDevice((got, total) => setBusy(`Downloading… ${bytes(got)}${total ? ` of ${bytes(total)}` : ''}`))
      changed()
    } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }
  const refresh = async () => {
    setError(null)
    setBusy('Refreshing…')
    try {
      const n = await refreshLocal(() => enableOnDevice(() => {}))
      setBusy(n > 0 ? `Applied ${n} changes.` : n < 0 ? 'Downloaded again (the Mac’s catalog was rebuilt).' : 'Already up to date.')
      setTimeout(() => setBusy(null), 2500)
      changed()
    } catch (e) { setError((e as Error).message); setBusy(null) }
  }
  const publish = async () => {
    setError(null)
    setBusy('Sharing…')
    try {
      const r = await publishLocal(true)
      setBusy(r ? `Shared: ${r.item_count} items, ${r.contributed_count} from this device.` : null)
      changed()
    } catch (e) { setError((e as Error).message); setBusy(null) }
  }
  const remove = async () => {
    await local.call('remove', 'library').catch(() => {})
    try { await clearOfflineImages() } catch { /* ignore */ }
    clearLocal()
    changed()
  }
  const iosNotInstalled = detectPlatform() === 'ios' && !isStandalone()

  return (
    <>
      <TopBar title="Catalogs" />
      <div className="stack">
        {error && <div className="error">{error}</div>}
        <section className="card">
          <h2>Use on this device</h2>
          <p className="muted small">Keeps a copy of the library catalog on this device so you can search and browse without
            your Mac or a network. Edits made offline are sent when you're back.</p>
          {available === false && <p className="warn small">{reason?.includes('another tab') ? reason
            : "This browser can't keep the catalog here (Private Browsing doesn't allow it)."}</p>}
          {iosNotInstalled && !state.enabled && <p className="warn small">On iPhone, add Shoebox to your Home Screen first: Safari
            removes data from sites that aren't installed after 7 days without use.</p>}
          {state.enabled ? (
            <>
              <p className="small"><span className="pill ok-pill">On this device</span> {state.items} items · downloaded {ago(state.downloadedAt)}
                {state.refreshedAt && state.refreshedAt !== state.downloadedAt ? ` · refreshed ${ago(state.refreshedAt)}` : ''}</p>
              <div className="row">
                <button className="btn" disabled={!online || !!busy} onClick={refresh}>Refresh now</button>
                <button className="btn" disabled={!online || !!busy} onClick={enable}>Download again</button>
                <button className="btn" disabled={!online || !!busy} onClick={publish}>Share this device's catalog</button>
                <button className="btn danger" disabled={!!busy} onClick={remove}>Remove downloaded data</button>
              </div>
            </>
          ) : (
            <div className="row"><button className="btn primary" disabled={!online || !!busy || available === false} onClick={enable}>
              Use on this device</button></div>
          )}
          {busy && <p className="small" role="status">{busy}</p>}
          {usage.quota ? <p className="muted small">Storage used by this app: {bytes(usage.usage)} of {bytes(usage.quota)} available.</p> : null}
        </section>
        <DeviceCatalogs state={state} />
        <OfflineImages onChange={() => navigator.storage?.estimate?.().then(setUsage).catch(() => {})} />
      </div>
    </>
  )
}

function OfflineImages({ onChange }: { onChange: () => void }) {
  const { online } = useApp()
  const this_year = new Date().getFullYear()
  const [year, setYear] = useState(String(this_year))
  const [status, setStatus] = useState<string | null>(null)
  const [saved, setSaved] = useState(0)
  useEffect(() => { savedCount().then(setSaved).catch(() => {}) }, [status])
  const go = async () => {
    try {
      const n = await downloadThumbs(year, (d, t) => setStatus(`Downloading thumbnails… ${d} of ${t}`))
      setStatus(n ? `Thumbnails for ${year} are on this device (${n}).` : `No photos from ${year}.`)
      onChange()
    } catch (e) { setStatus((e as Error).message) }
  }
  return (
    <section className="card">
      <h2>Photos offline</h2>
      <p className="muted small">Thumbnails you've scrolled past are kept automatically. Download a whole year to browse it
        offline. To keep a full-size copy, use <b>Save offline</b> in the viewer ({saved} saved).</p>
      <div className="row">
        <select value={year} onChange={(e) => setYear(e.target.value)} aria-label="Year to download">
          {Array.from({ length: 30 }, (_, i) => String(this_year - i)).map((y) => <option key={y}>{y}</option>)}
        </select>
        <button className="btn" disabled={!online} onClick={go}>Download thumbnails</button>
      </div>
      {status && <p className="small" role="status">{status}</p>}
    </section>
  )
}

const PLATFORM: Record<string, string> = { ios: 'iPhone', android: 'Android', macos: 'Mac', web: 'Browser' }

function DeviceCatalogs({ state }: { state: LocalState }) {
  const { online, changed } = useApp()
  const [published, setPublished] = useState<Published[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!online) return
    api<{ library: Published; devices: Published[] }>('/api/catalogs').then((r) => setPublished(r.devices)).catch(() => {})
  }, [online, state.devices])
  const downloaded = state.devices ?? {}
  const rows: Published[] = [...published]
  for (const [id, d] of Object.entries(downloaded)) {
    if (!rows.some((r) => r.device_id === id)) rows.push({ device_id: id, name: d.name, platform: d.platform, item_count: d.items, created_at: d.createdAt, kind: 'published' })
  }
  const open = new Set(state.open ?? (state.enabled ? ['library'] : []))
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    setError(null)
    try { await fn(); changed() } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }
  return (
    <section className="card">
      <h2>Device catalogs</h2>
      <p className="muted small">Each device shares its own catalog. Download the ones you want and tick them to search
        them together on this device, even with no connection.</p>
      {rows.length === 0 && <p className="muted small">No device has shared its catalog yet.</p>}
      <div className="list">
        {state.enabled && (
          <label className="catalog-row">
            <input type="checkbox" checked={open.has('library')} onChange={(e) => setOpen('library', e.target.checked)} aria-label="Search the library catalog" />
            <span><strong>Library</strong><span className="muted small"> · everything · {state.items} items</span></span>
          </label>
        )}
        {rows.map((c) => {
          const here = downloaded[c.device_id]
          const name = dbName(c.device_id)
          return (
            <div key={c.device_id} className="catalog-row">
              {here && <input type="checkbox" checked={open.has(name)} onChange={(e) => setOpen(name, e.target.checked)} aria-label={`Search ${c.name}`} />}
              <span style={{ flex: 1, minWidth: 0 }}>
                <strong>{c.name}</strong><span className="muted small"> · {PLATFORM[c.platform] ?? c.platform} · {c.contributed_count ?? c.item_count} of its own
                  {c.bytes ? ` · ${bytes(c.bytes)}` : ''} · shared {ago(c.created_at)}</span>
                {here && <span className="small ok"> · on this device ({here.items} items, downloaded {ago(here.downloadedAt)})</span>}
                {here && c.created_at > here.createdAt && <span className="pill">newer version available</span>}
              </span>
              <span className="row">
                {online && <button className="btn" disabled={!!busy} onClick={() => act(`dl-${c.device_id}`, () => downloadDeviceCatalog(c))}>
                  {busy === `dl-${c.device_id}` ? 'Downloading…' : here ? 'Update' : 'Download'}</button>}
                {here && <button className="btn" disabled={!!busy} onClick={() => act(`rm-${c.device_id}`, () => removeDeviceCatalog(c.device_id))}>Remove</button>}
              </span>
            </div>
          )
        })}
      </div>
      {error && <div className="error">{error}</div>}
      <div className="row">
        {(state.enabled || Object.keys(downloaded).length > 0) && (
          <label className="check"><input type="checkbox" checked={!!state.preferDevice}
                 onChange={(e) => setLocal({ preferDevice: e.target.checked })} /> Search on this device now, even while online</label>
        )}
        {online && published.length > 0 && <a className="btn" href="/api/catalogs/all.zip" download>Download all catalogs (.zip)</a>}
      </div>
    </section>
  )
}
