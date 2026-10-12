import { useEffect, useState } from 'react'
import { isStandalone } from '../components/InstallHint'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { detectPlatform } from '../lib/device'
import { ago, bytes } from '../lib/format'
import { gunzip, local } from '../local/client'
import { downloadResumable } from '../local/download'
import { clearLocal, getLocal, onLocal, setLocal, type LocalState } from '../local/state'

export async function enableOnDevice(onProgress: (got: number, total: number) => void): Promise<LocalState> {
  try { await navigator.storage?.persist?.() } catch { /* best effort */ }
  const { bytes: gz, headers } = await downloadResumable('/api/catalogs/library', onProgress)
  const { items } = await local.call<{ items: number }>('importCatalog', 'library', await gunzip(gz.buffer as ArrayBuffer))
  return setLocal({ enabled: true, downloadedAt: new Date().toISOString(), refreshedAt: new Date().toISOString(),
                    cursor: Number(headers.get('x-ops-cursor') ?? 0), epoch: headers.get('x-catalog-epoch'), items })
}

export function Catalogs() {
  const { online, changed } = useApp()
  const [state, setState] = useState(getLocal)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [usage, setUsage] = useState<{ usage?: number; quota?: number }>({})
  const [available, setAvailable] = useState<boolean | null>(null)
  useEffect(() => onLocal(setState), [])
  useEffect(() => {
    navigator.storage?.estimate?.().then(setUsage).catch(() => {})
    local.call<{ persistent: boolean }>('status').then((s) => setAvailable(s.persistent)).catch(() => setAvailable(false))
  }, [state])

  const enable = async () => {
    setError(null)
    setBusy('Downloading…')
    try {
      await enableOnDevice((got, total) => setBusy(`Downloading… ${bytes(got)}${total ? ` of ${bytes(total)}` : ''}`))
      changed()
    } catch (e) { setError((e as Error).message) } finally { setBusy(null) }
  }
  const remove = async () => {
    await local.call('remove', 'library').catch(() => {})
    try { await caches.delete('thumbs-v1') } catch { /* ignore */ }
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
          {available === false && <p className="warn small">This browser can't keep the catalog (Private Browsing doesn't allow it).</p>}
          {iosNotInstalled && !state.enabled && <p className="warn small">On iPhone, add Photoframing to your Home Screen first: Safari
            removes data from sites that aren't installed after 7 days without use.</p>}
          {state.enabled ? (
            <>
              <p className="small"><span className="pill ok-pill">On this device</span> {state.items} items · downloaded {ago(state.downloadedAt)}
                {state.refreshedAt && state.refreshedAt !== state.downloadedAt ? ` · refreshed ${ago(state.refreshedAt)}` : ''}</p>
              <div className="row">
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
      </div>
    </>
  )
}
