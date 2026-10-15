// "Use without your Mac" (phone app only): fetch the read-only key from the Mac once and keep it in the
// phone's Keychain / Keystore, so the app can browse the library from S3 when the Mac is off.
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { nativePlatform } from '../backup/native'
import { clearAccess, loadAccess, saveAccess, type ViewerAccess } from '../cloud/access'

export function browseUrl(): string | null {
  const p = nativePlatform()
  return p ? `${p === 'ios' ? 'capacitor://localhost' : 'https://localhost'}/browse/index.html#/library` : null
}

export function WithoutMac() {
  const [ready, setReady] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { loadAccess().then((a) => setReady(!!a)).catch(() => setReady(false)) }, [])
  if (!nativePlatform() || ready === null) return null
  const setUp = async () => {
    setBusy(true); setError(null)
    try { await saveAccess(await api<ViewerAccess>('/api/viewer/access')); setReady(true) }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <section className="card" aria-label="Use without your Mac">
      <h2>Use without your Mac</h2>
      <p className="muted small">Browse and search your library on this phone even when the Mac is off: pictures come straight
        from your S3 bucket with a read-only key. Nothing can be uploaded or changed that way.</p>
      {ready
        ? <>
            <p className="ok small" data-testid="without-mac-status">Ready on this phone.</p>
            <div className="row">
              <a className="btn" href={browseUrl()!}>Open read-only library</a>
              <button className="btn" disabled={busy} onClick={setUp}>Update key</button>
              <button className="btn" onClick={() => clearAccess().then(() => setReady(false))}>Remove from this phone</button>
            </div>
          </>
        : <div className="row"><button className="btn primary" disabled={busy} onClick={setUp}>{busy ? 'Setting up…' : 'Set up'}</button></div>}
      {error && <p className="error small">{error}</p>}
    </section>
  )
}
