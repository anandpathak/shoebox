// App state for viewer mode: the library comes from S3 (read-only key), no Mac involved.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Ctx, type AppState, type ViewerInfo } from '../lib/app'
import { getLocal } from '../local/state'
import { CloudDataSource, viewerState } from './source'

export function ViewerProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<CloudDataSource | null>(null)
  const [info, setInfo] = useState<Omit<ViewerInfo, 'refresh'>>({ status: 'loading', state: viewerState() })
  const [version, setVersion] = useState(0)
  const [online, setOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine)

  const refresh = useCallback(async (src: CloudDataSource | null) => {
    if (!src) return
    setInfo((i) => ({ ...i, status: i.state ? 'ready' : 'loading', error: undefined }))
    try {
      const state = await src.refresh((got, total) => setInfo((i) => ({ ...i, progress: [got, total] })))
      setInfo({ status: 'ready', state })
      setVersion((v) => v + 1)
    } catch (e) {
      const have = viewerState()          // keep browsing the copy already on the phone
      setInfo({ status: have ? 'ready' : 'error', state: have, error: (e as Error).message })
    }
  }, [])

  useEffect(() => {
    CloudDataSource.create().then((src) => {
      if (!src) { setInfo({ status: 'missing' }); return }
      setSource(src)
      void refresh(src)
    }).catch((e) => setInfo({ status: 'error', error: (e as Error).message }))
  }, [refresh])
  useEffect(() => {
    const on = () => { setOnline(navigator.onLine); if (navigator.onLine) void refresh(source) }
    addEventListener('online', on); addEventListener('offline', on)
    return () => { removeEventListener('online', on); removeEventListener('offline', on) }
  }, [refresh, source])

  const value = useMemo<AppState | null>(() => source && ({
    source, localState: getLocal(), health: null, online, authError: null, needsPairing: false, sync: null,
    refreshSync: () => {}, version, changed: () => setVersion((v) => v + 1),
    viewer: { ...info, refresh: () => void refresh(source) },
  }), [source, online, version, info, refresh])

  if (!value || info.status !== 'ready') return <ViewerGate info={info} retry={() => void refresh(source)} />
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

function ViewerGate({ info, retry }: { info: Omit<ViewerInfo, 'refresh'>; retry: () => void }) {
  const [got, total] = info.progress ?? [0, 0]
  return (
    <main className="pair">
      <section className="card" role="status" aria-label="Library without your Mac">
        {info.status === 'missing' ? (
          <>
            <h1>Set up browsing without your Mac</h1>
            <p className="muted">Connect to your Mac once, then go to <strong>Sync → Use without your Mac</strong>. After that
              this phone can browse your library even when the Mac is off.</p>
          </>
        ) : info.status === 'error' ? (
          <>
            <h1>Couldn't get your library</h1>
            <p className="error">{info.error}</p>
            <div className="row"><button className="btn primary" onClick={retry}>Try again</button></div>
          </>
        ) : (
          <>
            <h1>Getting your library…</h1>
            <p className="muted">{total ? `${(got / 1e6).toFixed(1)} of ${(total / 1e6).toFixed(1)} MB` : 'Checking for the latest copy'}</p>
          </>
        )}
      </section>
    </main>
  )
}
