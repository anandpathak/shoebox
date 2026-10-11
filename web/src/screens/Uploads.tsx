import { useCallback, useEffect, useRef, useState } from 'react'
import * as tus from 'tus-js-client'
import { api, ApiError } from '../api/client'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { getDevice } from '../lib/device'
import { ago, bytes } from '../lib/format'

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
        <DeviceUploads onDone={() => { refresh(); changed() }} />
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
      <p className="muted small">Runs on the Mac, with the same steps as <code>photoctl archive</code>. Only one run at a time.</p>
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
  useEffect(() => { setState(run); finished.current = run.status !== 'running' }, [run])
  useEffect(() => {
    if (run.status !== 'running') return
    const es = new EventSource(`/api/runs/${run.run_id}/events`)
    es.onmessage = (m) => {
      const e = JSON.parse(m.data)
      setState((s) => {
        const next = { ...s }
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
  }, [run.run_id, run.status, onFinished])
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

interface UploadRow { name: string; size: number; sent: number; status: 'uploading' | 'done' | 'error' | 'paused'; error?: string }

function DeviceUploads({ onDone }: { onDone: () => void }) {
  const [rows, setRows] = useState<UploadRow[]>([])
  const input = useRef<HTMLInputElement>(null)
  const start = (files: FileList | null) => {
    if (!files?.length) return
    const dev = getDevice()
    const list = Array.from(files)
    setRows((r) => [...list.map((f) => ({ name: f.name, size: f.size, sent: 0, status: 'uploading' as const })), ...r])
    let left = list.length
    list.forEach((file) => {
      const update = (patch: Partial<UploadRow>) => setRows((rs) => rs.map((x) => (x.name === file.name && x.size === file.size ? { ...x, ...patch } : x)))
      const up = new tus.Upload(file, {
        endpoint: '/api/uploads/files',
        chunkSize: 8 * 1024 * 1024,
        retryDelays: [0, 1000, 3000, 5000, 10000, 20000],
        removeFingerprintOnSuccess: true,
        metadata: { filename: file.name, filetype: file.type, device_id: dev.device_id, lastModified: String(file.lastModified) },
        onProgress: (sent) => update({ sent }),
        onSuccess: () => { update({ status: 'done', sent: file.size }); if (--left === 0) setTimeout(onDone, 1500) },
        onError: (err) => { update({ status: 'error', error: err.message }); --left },
      })
      up.findPreviousUploads().then((prev) => { if (prev.length) up.resumeFromPreviousUpload(prev[0]); up.start() })
    })
  }
  return (
    <section className="card">
      <h2>Upload from this device</h2>
      <p className="muted small">Pick photos and videos. Large files upload in 8 MB pieces and carry on after a dropped connection.
        Some phones remove the location from shared photos; those are flagged so you know.</p>
      <input ref={input} type="file" accept="image/*,video/*" multiple hidden onChange={(e) => { start(e.target.files); e.target.value = '' }}
             aria-label="Choose photos and videos" />
      <div className="row"><button className="btn primary" onClick={() => input.current?.click()}>Choose photos and videos</button></div>
      {rows.length > 0 && (
        <div className="list">
          {rows.map((r, i) => (
            <div key={`${r.name}-${i}`} className="upload-row">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</div>
                <div className="progress thin"><span style={{ width: `${r.size ? (r.sent / r.size) * 100 : 0}%` }} /></div>
              </div>
              <span className={r.status === 'error' ? 'pill' : 'muted small'} title={r.error}>
                {r.status === 'done' ? 'Uploaded' : r.status === 'error' ? 'Failed' : `${bytes(r.sent)} / ${bytes(r.size)}`}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

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
