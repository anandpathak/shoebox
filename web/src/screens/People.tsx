import { useEffect, useState } from 'react'
import type { Person } from '../api/types'
import { TopBar } from '../components/Shell'
import { WhoIsThis } from '../components/WhoIsThis'
import { useApp } from '../lib/app'
import { go } from '../lib/router'

export function People() {
  const { source, version, changed, online, localState } = useApp()
  const [people, setPeople] = useState<Person[] | null>(null)
  const [covers, setCovers] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    source.people().then(setPeople).catch((e) => setError(e.message))
  }, [source, version])
  useEffect(() => {
    if (!people) return
    for (const p of people.slice(0, 60)) {
      if (!p.name || covers[p.person_id]) continue
      source.search({ person: p.name, limit: 1 }).then((r) => {
        const first = r.items[0]
        if (first) setCovers((c) => ({ ...c, [p.person_id]: source.thumbUrl(first.photo_id) }))
      }).catch(() => {})
    }
  }, [people, source]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <TopBar title="People" />
      {error && <div className="error">{error}</div>}
      {online && !localState.preferDevice && people && <div className="stack"><WhoIsThis people={people} onChanged={changed} /></div>}
      {people && people.length === 0 && (
        <div className="empty"><strong>No people yet</strong>
          <span>Names you gave faces in Apple Photos appear here, and so do faces you name above. You can also add people to any photo.</span></div>
      )}
      <div className="people-list stack">
        {people?.map((p) => (
          <PersonRow key={p.person_id} person={p} cover={covers[p.person_id]} others={people.filter((o) => o.person_id !== p.person_id)}
                     onChanged={changed} />
        ))}
      </div>
    </>
  )
}

function PersonRow({ person, cover, others, onChanged }: { person: Person; cover?: string; others: Person[]; onChanged: () => void }) {
  const { source } = useApp()
  const [mode, setMode] = useState<'view' | 'rename' | 'merge'>('view')
  const [name, setName] = useState(person.name ?? '')
  const [into, setInto] = useState('')
  const [error, setError] = useState<string | null>(null)
  const done = () => { setMode('view'); setError(null); onChanged() }
  const fail = (e: Error) => setError(e.message)

  return (
    <div className="card person-card">
      <div className="person-head">
        <button className="avatar" onClick={() => person.name && go(`library?person=${encodeURIComponent(person.name)}`)}
                aria-label={`Photos of ${person.name}`}>
          {cover ? <img src={cover} alt="" loading="lazy" /> : <span>{(person.name ?? '?').slice(0, 1)}</span>}
        </button>
        <div className="person-text">
          <strong>{person.name ?? 'Unnamed'}</strong>
          <span className="muted small">{person.photos} {person.photos === 1 ? 'photo' : 'photos'}</span>
        </div>
        {mode === 'view' && (
          <div className="row">
            <button className="btn" onClick={() => setMode('rename')}>Rename</button>
            {others.length > 0 && <button className="btn" onClick={() => setMode('merge')}>Merge…</button>}
          </div>
        )}
      </div>
      {mode === 'rename' && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); source.edit.renamePerson(person.person_id, name.trim()).then(done, fail) }}>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} aria-label={`New name for ${person.name}`} autoFocus />
          <button className="btn primary" disabled={!name.trim()}>Save</button>
          <button type="button" className="btn" onClick={() => setMode('view')}>Cancel</button>
        </form>
      )}
      {mode === 'merge' && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); source.edit.mergePerson(person.person_id, into).then(done, fail) }}>
          <select value={into} onChange={(e) => setInto(e.target.value)} aria-label={`Merge ${person.name} into`}>
            <option value="">Same person as…</option>
            {others.map((o) => <option key={o.person_id} value={o.person_id}>{o.name} ({o.photos})</option>)}
          </select>
          <button className="btn primary" disabled={!into}>Merge</button>
          <button type="button" className="btn" onClick={() => setMode('view')}>Cancel</button>
          <span className="muted small">Their photos move to the person you pick.</span>
        </form>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  )
}
