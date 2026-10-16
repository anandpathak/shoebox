import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { uploaderId } from '../components/RegisterDevice'
import { detectPlatform } from '../lib/device'
import { ago, bytes } from '../lib/format'
import { sha256File } from '../lib/hash'
import { enqueue, list, processQueue, subscribe, type Progress, type Queued } from '../lib/uploadQueue'

interface RunInfo {
  run_id: string; kind?: string; label?: string; status: 'running' | 'done' | 'failed' | 'cancelled'
  todo: number | null; processed: number; counts: Record<string, number>; messages: string[]
  started_at: string | null; ended_at?: string | null; error?: string | null; cancellable?: boolean
  failed?: { source_ref: string; status: string; error: string | null }[]
}

type Mode = 'selected' | 'album' | 'range' | 'all'

export function Uploads() {
  const { health, changed } = useApp()
  const [active, setActive] = useState<RunInfo | null>(null)
  const [history, setHistory] = useState<RunInfo[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    api<RunInfo | null>('/api/runs/active').then((r) => setActive((cur) => r ?? (cur?.status === 'running' ? null : cur))).catch(() => {})
    api<RunInfo[]>('/api/runs').then(setHistory).catch(() => {})
  }, [])
  useEffect(refresh, [refresh])

  return (
    <>
      <TopBar title="Uploads" />
      <div className="stack">
        {error && <div className="error">{error}</div>}
        <AppleCard mac={health?.device.name ?? 'your Mac'} busy={active?.status === 'running'}
                   onStarted={(r) => { setActive(r); setError(null) }} onError={setError} />
        {active && <LiveRun run={active} onUpdate={setActive} onFinished={() => { refresh(); changed() }} />}
        <DeviceUploads deviceId={uploaderId(health?.via, health?.device.device_id)} onDone={() => { refresh(); changed() }} />
        <History runs={history.filter((r) => r.run_id !== active?.run_id)} onRetry={(r) => setActive(r)} onError={setError} />
      </div>
    </>
  )
}

function AppleCard({ mac, busy, onStarted, onError }: { mac: string; busy: boolean; onStarted: (r: RunInfo) => void; onError: (e: string) => void }) {
  const [mode, setMode] = useState<Mode>('selected')
  const [album, setAlbum] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [dry, setDry] = useState(false)
  const [starting, setStarting] = useState(false)
  const start = async () => {
    setStarting(true)
    try {
      const body: Record<string, unknown> = { mode, dry_run: dry }
      if (mode === 'album') body.album = album
      if (mode === 'range') { if (from) body.from = from; if (to) body.to = to }
      onStarted(await api<RunInfo>('/api/uploads/apple-photos', { method: 'POST', json: body }))
    } catch (e) {
      const err = e as ApiError
      onError(err.status === 409 ? 'An archive run is already going on this Mac. Its progress is shown below.' : err.message)
    } finally { setStarting(false) }
  }
  const ready = mode !== 'album' || album.trim() !== ''
  return (
    <section className="card">
      <h2>Archive from Apple Photos on {mac}</h2>
      <p className="muted small">Runs on the Mac, with the same steps as <code>shoebox archive</code>. Only one run at a time.</p>
      <label className="field">What to archive
        <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} aria-label="What to archive">
          <option value="selected">What's selected in Photos on the Mac</option>
          <option value="album">An album</option>
          <option value="range">Photos taken between two dates</option>
          <option value="all">Everything not archived yet</option>
        </select>
      </label>
      {mode === 'album' && <label className="field">Album name<input type="text" value={album} onChange={(e) => setAlbum(e.target.value)} placeholder="Goa 2024" /></label>}
      {mode === 'range' && (
        <div className="row">
          <label className="field">From<input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label className="field">To<input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        </div>
      )}
      <label className="check"><input type="checkbox" checked={dry} onChange={(e) => setDry(e.target.checked)} /> Dry run (count only, upload nothing)</label>
      <div className="row"><button className="btn primary" onClick={start} disabled={busy || starting || !ready}>
        {starting ? 'Starting…' : busy ? 'A run is in progress' : dry ? 'Count photos' : 'Start archive'}</button></div>
    </section>
  )
}

function LiveRun({ run, onUpdate, onFinished }: { run: RunInfo; onUpdate: (r: RunInfo) => void; onFinished: () => void }) {
  const [state, setState] = useState(run)
  const finished = useRef(false)
  // reset only when a different run is shown; status updates for this run come back through `run` too
  useEffect(() => { setState(run); finished.current = run.status !== 'running' }, [run.run_id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (finished.current) return
    const es = new EventSource(`/api/runs/${run.run_id}/events`)
    es.onmessage = (m) => {
      const e = JSON.parse(m.data)
      setState((s) => {
        const next = { ...s }
        // every (re)connection replays the run from its first line: start counting afresh
        if (e.event === 'accepted') { next.processed = 0; next.counts = {}; next.messages = [] }
        if (e.event === 'start') next.todo = e.todo
        if (e.event === 'item') {
          next.processed = (s.processed ?? 0) + 1
          next.counts = { ...s.counts, [e.status]: (s.counts?.[e.status] ?? 0) + 1 }
        }
        if (e.event === 'message') next.messages = [...(s.messages ?? []), e.text]
        if (e.event === 'end') next.status = e.cancelled ? 'cancelled' : 'done'
        return next
      })
    }
    es.addEventListener('close', () => { es.close(); if (!finished.current) { finished.current = true; onFinished() } })
    return () => es.close()
  }, [run.run_id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { onUpdate(state) }, [state.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const pct = state.todo ? Math.round((state.processed / state.todo) * 100) : 0
  const failed = (state.counts?.failed ?? 0) + (state.counts?.missing_original ?? 0)
  return (
    <section className="card" aria-live="polite">
      <h2>{state.status === 'running' ? 'Archiving…' : state.status === 'cancelled' ? 'Cancelled' : 'Finished'}</h2>
      <p className="muted small">{state.label ?? 'Archive run'}{state.started_at ? ` · started ${ago(state.started_at)}` : ''}</p>
      <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }} /></div>
      <p className="small">{state.todo == null ? 'Reading the Photos library…'
        : `${state.processed} of ${state.todo} done${failed ? ` · ${failed} failed` : ''}`}</p>
      {state.messages?.map((m, i) => <p key={i} className="muted small">{m}</p>)}
      {state.status === 'running' && state.cancellable !== false && (
        <div className="row"><button className="btn" onClick={() => api(`/api/runs/${state.run_id}/cancel`, { method: 'POST' }).catch(() => {})}>
          Cancel after the current items</button></div>
      )}
    </section>
  )
}

function DeviceUploads({ deviceId, onDone }: { deviceId: string; onDone: () => void }) {
  const { online } = useApp()
  const [queued, setQueued] = useState<Queued[]>([])
  const [progress, setProgress] = useState<Progress>({})
  const [checking, setChecking] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const reload = useCallback(() => { list().then(setQueued).catch(() => {}) }, [])
  useEffect(() => subscribe(setProgress), [])
  useEffect(reload, [reload, progress])
  const run = useCallback(async () => { if (await processQueue()) { reload(); setTimeout(onDone, 1500) } }, [onDone, reload])
  useEffect(() => { if (online) run() }, [online, run])

  // Online: fingerprint each file on the phone and send only what the archive doesn't have yet.
  // Offline: queue everything (the archive is content-addressed, so a duplicate is never stored twice).
  const pick = async (files: FileList | null) => {
    if (!files?.length) return
    let todo = Array.from(files)
    if (online) {
      try {
        const hashes: string[] = []
        for (const [i, f] of todo.entries()) {
          setChecking(`Checking ${i + 1} of ${todo.length}…`)
          hashes.push(await sha256File(f))
        }
        const missing = new Set<string>()
        for (let i = 0; i < hashes.length; i += 500) {
          const r = await api<{ missing: string[] }>('/api/backup/check', { method: 'POST', json: { hashes: hashes.slice(i, i + 500) } })
          r.missing.forEach((h) => missing.add(h))
        }
        const skipped = todo.length - todo.filter((_, i) => missing.has(hashes[i])).length
        todo = todo.filter((_, i) => missing.has(hashes[i]))
        setChecking(skipped ? `${skipped} already backed up · ${todo.length} to upload` : null)
      } catch {
        setChecking(null)                       // couldn't check: just upload them all
      }
    }
    if (todo.length) {
      await enqueue(todo, deviceId)
      reload()
      if (online) run()
    }
  }
  const rows = [
    ...queued.filter((q) => progress[q.id]?.status !== 'done')
      .map((q) => ({ id: q.id, name: q.name, size: q.size, ...(progress[q.id] ?? { sent: 0, status: 'waiting' as const }) })),
    ...Object.entries(progress).filter(([, p]) => p.status === 'done')
      .map(([id, p]) => ({ id, name: doneNames.get(id) ?? 'Uploaded file', size: p.sent, ...p })),
  ]
  for (const q of queued) doneNames.set(q.id, q.name)
  return (
    <section className="card">
      <h2>Upload from this device</h2>
      <p className="muted small">Pick photos and videos. Large files upload in 8 MB pieces and carry on after a dropped connection.
        If your Mac can't be reached they wait here and upload by themselves later.
        Some phones remove the location from shared photos; those are flagged so you know.</p>
      <input ref={input} type="file" accept="image/*,video/*" multiple hidden onChange={(e) => { pick(e.target.files); e.target.value = '' }}
             aria-label="Choose photos and videos" />
      <div className="row"><button className="btn primary" onClick={() => input.current?.click()}>Choose photos and videos</button></div>
      {detectPlatform() === 'android' && (
        <p className="muted small" data-testid="android-location-tip">On Android, photos picked here arrive without their location
          (an Android privacy rule). To keep it, use the Shoebox app's <strong>Sync → Back up this phone</strong>.</p>)}
      {checking && <p className="small" role="status">{checking}</p>}
      {rows.length > 0 && (
        <div className="list">
          {rows.map((r) => (
            <div key={r.id} className="upload-row">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
                <div className="progress thin"><span style={{ width: `${r.size ? (r.sent / r.size) * 100 : 0}%` }} /></div>
              </div>
              <span className="muted small" title={r.error}>
                {r.status === 'done' ? 'Uploaded' : r.status === 'uploading' ? `${bytes(r.sent)} / ${bytes(r.size)}`
                  : online ? 'Waiting…' : 'Waiting for your Mac'}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

const doneNames = new Map<string, string>()

function History({ runs, onRetry, onError }: { runs: RunInfo[]; onRetry: (r: RunInfo) => void; onError: (e: string) => void }) {
  if (!runs.length) return null
  const retry = async (r: RunInfo) => {
    const failed = r.failed ?? []
    const uuids = failed.filter((f) => f.source_ref.startsWith('applephotos:')).map((f) => f.source_ref.slice(12))
    try {
      if (uuids.length) onRetry(await api<RunInfo>('/api/uploads/apple-photos', { method: 'POST', json: { mode: 'uuids', uuids } }))
      if (failed.some((f) => f.source_ref.startsWith('upload:'))) await api('/api/uploads/files/retry', { method: 'POST' })
    } catch (e) { onError((e as Error).message) }
  }
  return (
    <section className="card">
      <h2>Recent runs</h2>
      <div className="list">
        {runs.slice(0, 15).map((r) => {
          const failed = r.failed?.length ?? 0
          return (
            <div key={r.run_id} className="history-row">
              <div style={{ minWidth: 0 }}>
                <div className="small">{r.label ?? (r.kind === 'upload' ? 'Uploaded files' : 'Archive run')} · {ago(r.started_at)}</div>
                <div className="muted small">{Object.entries(r.counts ?? {}).map(([k, v]) => `${v} ${k.replace('_', ' ')}`).join(' · ') || 'nothing to do'}
                  {r.status !== 'done' ? ` · ${r.status}` : ''}</div>
                {failed > 0 && <details className="small"><summary>{failed} failed</summary>
                  {r.failed!.slice(0, 20).map((f) => <div key={f.source_ref} className="mono muted">{f.source_ref}: {f.error ?? f.status}</div>)}</details>}
              </div>
              {failed > 0 && <button className="btn" onClick={() => retry(r)}>Retry failed</button>}
            </div>
          )
        })}
      </div>
    </section>
  )
}
