import { useEffect, useState } from 'react'
import { isStandalone } from '../components/InstallHint'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { detectPlatform } from '../lib/device'
import { ago, bytes } from '../lib/format'
import { local } from '../local/client'
import { enableOnDevice } from '../local/enable'
import { clearOfflineImages, downloadThumbs, savedCount } from '../local/offline'
import { refreshLocal } from '../local/refresh'
import { clearLocal, getLocal, onLocal } from '../local/state'


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
          {iosNotInstalled && !state.enabled && <p className="warn small">On iPhone, add Photoframing to your Home Screen first: Safari
            removes data from sites that aren't installed after 7 days without use.</p>}
          {state.enabled ? (
            <>
              <p className="small"><span className="pill ok-pill">On this device</span> {state.items} items · downloaded {ago(state.downloadedAt)}
                {state.refreshedAt && state.refreshedAt !== state.downloadedAt ? ` · refreshed ${ago(state.refreshedAt)}` : ''}</p>
              <div className="row">
                <button className="btn" disabled={!online || !!busy} onClick={refresh}>Refresh now</button>
                <button className="btn" disabled={!online || !!busy} onClick={enable}>Download again</button>
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
