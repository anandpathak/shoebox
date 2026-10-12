import { useEffect, useState } from 'react'
import type { Device, ParsedQuery, Person } from '../api/types'
import { Icon } from './Icon'

export interface Filters { q: string; type: '' | 'photo' | 'video'; fav: boolean; device: string; person: string; order: '' | 'new' }
export const EMPTY: Filters = { q: '', type: '', fav: false, device: '', person: '', order: '' }

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
  'November', 'December']

export function describe(q: ParsedQuery | undefined): string {
  if (!q) return ''
  const bits: string[] = []
  if (q.date_from && q.date_to) {
    const [y, m] = q.date_from.split('-')
    const toY = Number(q.date_to.slice(0, 4))
    bits.push(m === '01' && toY === Number(y) + 1 ? y : `${MONTHS[Number(m) - 1]} ${y}`)
  } else if (q.month) bits.push(`${MONTHS[Number(q.month) - 1]}, any year`)
  if (q.media_type) bits.push(q.media_type === 'video' ? 'videos' : 'photos')
  if (q.terms.length) bits.push(`“${q.terms.join(' ')}”`)
  return bits.join(' · ')
}

export function SearchBar({ value, onChange, devices, people, understood, canRank }: {
  value: Filters; onChange: (f: Filters) => void; devices: Device[]; people: Person[]; understood: string
  canRank?: boolean    // the Mac can rank typed searches by meaning
}) {
  const [text, setText] = useState(value.q)
  useEffect(() => setText(value.q), [value.q])
  useEffect(() => {
    if (text === value.q) return
    const t = setTimeout(() => onChange({ ...value, q: text }), 250)
    return () => clearTimeout(t)
  }, [text]) // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch })
  const active = value.q || value.type || value.fav || value.device || value.person
  return (
    <div className="searchbar">
      <label className="search">
        <Icon name="search" size={18} />
        <input type="search" placeholder="Search anything: “kids in the snow”, people, places, “december 2024”…" value={text}
               onChange={(e) => setText(e.target.value)} aria-label="Search the library" enterKeyHint="search" />
      </label>
      <div className="chips" role="toolbar" aria-label="Filters">
        {(['', 'photo', 'video'] as const).map((t) => (
          <button key={t || 'all'} className="chip" aria-pressed={value.type === t} onClick={() => set({ type: t })}>
            {t === '' ? 'All' : t === 'photo' ? 'Photos' : 'Videos'}
          </button>
        ))}
        <button className="chip" aria-pressed={value.fav} onClick={() => set({ fav: !value.fav })}>Favourites</button>
        {devices.length > 1 && devices.map((d) => (
          <button key={d.device_id} className="chip" aria-pressed={value.device === d.device_id}
                  onClick={() => set({ device: value.device === d.device_id ? '' : d.device_id })}>
            {d.name ?? d.device_id}
          </button>
        ))}
        {people.slice(0, 8).map((p) => (
          <button key={p.person_id} className="chip" aria-pressed={value.person === p.name}
                  onClick={() => set({ person: value.person === p.name ? '' : p.name ?? '' })}>
            {p.name}
          </button>
        ))}
        {active && <button className="chip" onClick={() => { setText(''); onChange(EMPTY) }}>Clear</button>}
      </div>
      {canRank && value.q.trim() && (
        <div className="chips" role="toolbar" aria-label="Order">
          <button className="chip" aria-pressed={value.order === ''} onClick={() => set({ order: '' })}>Best matches</button>
          <button className="chip" aria-pressed={value.order === 'new'} onClick={() => set({ order: 'new' })}>Newest first</button>
        </div>
      )}
      {understood && <p className="understood muted small">Showing {understood}</p>}
    </div>
  )
}
