// Shown on a phone that reached the Mac over Wi-Fi but isn't paired yet.
import { useEffect, useState } from 'react'
import { defaultName, detectPlatform, getDevice } from '../lib/device'
import { cleanCode, codeFromUrl, pairWith } from '../lib/pairing'

const fromUrl = codeFromUrl()

export function PairPhone() {
  const [code, setCode] = useState(fromUrl ?? '')
  const [name, setName] = useState(() => getDevice().name || defaultName(detectPlatform()))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pair = async (c: string) => {
    setBusy(true)
    setError(null)
    try {
      await pairWith(cleanCode(c), name.trim() || 'Phone')
      location.reload()
    } catch (e) { setError((e as Error).message); setBusy(false) }
  }
  useEffect(() => { if (fromUrl) void pair(fromUrl) }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="pair">
      <section className="card" role="dialog" aria-label="Pair this phone">
        <h1>Pair this phone</h1>
        <p className="muted small">On the Mac, <code>shoebox serve --lan</code> shows a code (run <code>shoebox pair</code> for a
          new one). Scanning its QR code pairs this phone straight away.</p>
        <form onSubmit={(e) => { e.preventDefault(); void pair(code) }}>
          <label className="field">Code<input value={code} onChange={(e) => setCode(e.target.value)} autoCapitalize="characters"
            autoComplete="one-time-code" placeholder="ABCD-EFGH" aria-label="Pairing code" /></label>
          <label className="field">This phone's name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <div className="row"><button className="btn primary" disabled={busy || cleanCode(code).length < 8}>
            {busy ? 'Pairing…' : 'Pair'}</button></div>
        </form>
        {error && <div className="error" role="alert">{error}</div>}
      </section>
    </main>
  )
}
