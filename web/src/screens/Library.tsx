import { useEffect, useMemo, useState } from 'react'
import type { Device, Person } from '../api/types'
import { Grid } from '../components/Grid'
import { describe, EMPTY, SearchBar, type Filters } from '../components/SearchBar'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { go, type Route } from '../lib/router'
import { rememberList } from '../lib/listCache'
import { useSearch } from '../lib/useSearch'

function fromQuery(q: URLSearchParams): Filters {
  const type = q.get('type')
  return { q: q.get('q') ?? '', type: type === 'photo' || type === 'video' ? type : '', fav: q.get('fav') === '1',
           device: q.get('device') ?? '', person: q.get('person') ?? '' }
}

function toQuery(f: Filters): string {
  const p = new URLSearchParams()
  if (f.q) p.set('q', f.q)
  if (f.type) p.set('type', f.type)
  if (f.fav) p.set('fav', '1')
  if (f.device) p.set('device', f.device)
  if (f.person) p.set('person', f.person)
  const s = p.toString()
  return s ? `?${s}` : ''
}

export function Library({ route }: { route: Route }) {
  const { source, version } = useApp()
  const filters = fromQuery(route.query)
  const [devices, setDevices] = useState<Device[]>([])
  const [people, setPeople] = useState<Person[]>([])
  useEffect(() => {
    source.devices().then(setDevices).catch(() => {})
    source.people().then(setPeople).catch(() => {})
  }, [source, version])

  const params = useMemo(() => ({
    q: filters.q || undefined, type: filters.type || undefined, fav: filters.fav || undefined,
    device: filters.device || undefined, person: filters.person || undefined,
  }), [filters.q, filters.type, filters.fav, filters.device, filters.person])
  const { items, loading, error, done, more, query } = useSearch(source, params, version)
  const searching = JSON.stringify(filters) !== JSON.stringify(EMPTY)
  useEffect(() => rememberList(toQuery(filters), items.map((i) => i.photo_id)), [items, filters.q, filters.type, filters.fav, filters.device, filters.person]) // eslint-disable-line react-hooks/exhaustive-deps
  const open = (id: string) => go(`media/${id}${toQuery(filters)}`)

  return (
    <>
      <TopBar title="Library">
        <SearchBar value={filters} onChange={(f) => go(`library${toQuery(f)}`, true)} devices={devices} people={people}
                   understood={searching ? describe(query) : ''} />
      </TopBar>
      {error && <div className="error">{error}</div>}
      {!loading && !error && items.length === 0
        ? (searching
            ? <div className="empty"><strong>No matches</strong><span>Try fewer words, or clear the filters.</span></div>
            : <div className="empty"><strong>No photos yet</strong><span>Archive some from Apple Photos and they'll appear here.</span></div>)
        : <Grid items={items} source={source} onOpen={open} onEnd={more} loading={loading} done={done} />}
    </>
  )
}
