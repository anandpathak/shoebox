// "Who's this?": groups of faces the Mac found that don't have a name yet. Naming a group tags every
// photo in it (and later photos of the same person), so the name becomes searchable everywhere.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Person } from '../api/types'

interface Group { cluster: number; faces: number; photos: number; sample: number[] }
interface Status {
  enabled: boolean; total: number; analyzed: number; running: boolean; last_error: string | null
  unnamed_groups: number; faces: Record<string, number>
  ai?: { installed: boolean; search_ready: number; captions: number; step: string | null }
}

export function analysisLine(s: Status): string {
  if (s.total === 0) return ''
  const named = (s.faces.confirmed ?? 0) + (s.faces.auto ?? 0)
  const left = s.total - s.analyzed
  return [
    left > 0 ? `Looked at ${s.analyzed.toLocaleString()} of ${s.total.toLocaleString()} photos${s.running ? '…' : ''}`
      : `All ${s.total.toLocaleString()} photos looked at`,
    named ? `${named.toLocaleString()} faces recognised` : '',
    s.ai?.installed && s.ai.captions ? `${s.ai.captions.toLocaleString()} described` : '',
    s.ai?.step === 'loading models' ? 'loading AI models…' : '',
  ].filter(Boolean).join(' · ')
}

export function WhoIsThis({ people, onChanged }: { people: Person[]; onChanged: () => void }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [groups, setGroups] = useState<Group[]>([])
  const [available, setAvailable] = useState(true)
  const load = useCallback(() => {
    api<Status>('/api/analysis/status').then(setStatus).catch(() => setAvailable(false))
    api<Group[]>('/api/faces/groups?limit=40').then(setGroups).catch(() => setAvailable(false))
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => {                               // keep the count moving while the Mac is analysing
    if (!status?.running) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [status?.running, load])
  if (!available || !status) return null

  const done = () => { load(); onChanged() }
  return (
    <section className="card who" aria-label="Who's this?">
      <div className="who-head">
        <h2>Who's this?</h2>
        <span className="muted small" data-testid="analysis-status">{analysisLine(status)}</span>
      </div>
      {status.last_error && <p className="error small">Analysis stopped: {status.last_error}</p>}
      {!status.enabled && status.analyzed < status.total &&
        <p className="muted small">Photo analysis is off on this Mac. Run <code>photoctl analyze</code> there to label photos and find faces.</p>}
      {groups.length === 0
        ? <p className="muted small">{Object.values(status.faces).some((n) => n > 0) ? 'Every face found so far has a name.'
            : 'Faces appear here once the Mac has looked at your photos.'}</p>
        : <p className="muted small">Name someone once and all their photos can be searched by name, on every device.</p>}
      <datalist id="people-names">{people.filter((p) => p.name).map((p) => <option key={p.person_id} value={p.name!} />)}</datalist>
      <div className="who-list">
        {groups.map((g) => <GroupRow key={g.cluster} group={g} onDone={done} />)}
      </div>
    </section>
  )
}

function GroupRow({ group, onDone }: { group: Group; onDone: () => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null)
    try { await fn(); onDone() } catch (e) { setError((e as Error).message); setBusy(false) }
  }
  return (
    <div className="who-row" data-testid="face-group">
      <div className="who-faces">
        {group.sample.slice(0, 4).map((f) => <img key={f} src={`/api/faces/${f}.jpg`} alt="" loading="lazy" />)}
      </div>
      <div className="who-body">
        <span className="muted small">{group.photos} {group.photos === 1 ? 'photo' : 'photos'}</span>
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) act(() => api('/api/faces/name', { method: 'POST', json: { cluster: group.cluster, name: name.trim() } })) }}>
          <input type="text" list="people-names" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name"
                 aria-label={`Name for this person (${group.photos} photos)`} disabled={busy} />
          <button className="btn primary" disabled={busy || !name.trim()}>Save</button>
          <button type="button" className="btn" disabled={busy} title="Not a face, or someone you don't want to name"
                  onClick={() => act(() => api('/api/faces/ignore', { method: 'POST', json: { cluster: group.cluster } }))}>Not a person</button>
        </form>
        {error && <div className="error small">{error}</div>}
      </div>
    </div>
  )
}
