import { useEffect, useMemo, useState } from 'react'
import type { Device, Person } from '../api/types'
import { Grid } from '../components/Grid'
import { InstallHint } from '../components/InstallHint'
import { describe, EMPTY, SearchBar, type Filters } from '../components/SearchBar'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { go, type Route } from '../lib/router'
import { rememberList } from '../lib/listCache'
import { useSearch } from '../lib/useSearch'

function fromQuery(q: URLSearchParams): Filters {
  const type = q.get('type')
  return { q: q.get('q') ?? '', type: type === 'photo' || type === 'video' ? type : '', fav: q.get('fav') === '1',
           device: q.get('device') ?? '', person: q.get('person') ?? '', order: q.get('order') === 'new' ? 'new' : '' }
}

function toQuery(f: Filters): string {
  const p = new URLSearchParams()
  if (f.q) p.set('q', f.q)
  if (f.type) p.set('type', f.type)
  if (f.fav) p.set('fav', '1')
  if (f.device) p.set('device', f.device)
  if (f.person) p.set('person', f.person)
  if (f.q && f.order) p.set('order', f.order)
  const s = p.toString()
  return s ? `?${s}` : ''
}

/** "Showing best matches for “kids in snow” · with Asha" */
export function understood(what: string, meta: { mode?: string; people?: string[] }): string {
  const people = meta.people?.length ? ` · with ${meta.people.join(' and ')}` : ''
  if (meta.mode !== 'smart') return what + people
  return `best matches for ${what || 'your search'}${people}`
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

  const canRank = source.kind === 'server'
  const params = useMemo(() => ({
    q: filters.q || undefined, type: filters.type || undefined, fav: filters.fav || undefined,
    device: filters.device || undefined, person: filters.person || undefined,
    smart: canRank && filters.order !== 'new' ? true : undefined,
  }), [filters.q, filters.type, filters.fav, filters.device, filters.person, filters.order, canRank])
  const { items, loading, error, done, more, query, meta } = useSearch(source, params, version)
  const searching = JSON.stringify(filters) !== JSON.stringify(EMPTY)
  useEffect(() => rememberList(toQuery(filters), items.map((i) => i.photo_id)), [items, filters.q, filters.type, filters.fav, filters.device, filters.person, filters.order]) // eslint-disable-line react-hooks/exhaustive-deps
  const open = (id: string) => go(`media/${id}${toQuery(filters)}`)

  return (
    <>
      <TopBar title="Library">
        <SearchBar value={filters} onChange={(f) => go(`library${toQuery(f)}`, true)} devices={devices} people={people}
                   understood={searching ? understood(describe(query), meta) : ''} canRank={canRank && meta.mode !== 'keywords'} />
      </TopBar>
      <InstallHint />
      {error && <div className="error">{error}</div>}
      {!loading && !error && items.length === 0
        ? (searching
            ? <div className="empty"><strong>No matches</strong><span>Try fewer words, or clear the filters.</span></div>
            : <div className="empty"><strong>No photos yet</strong><span>Archive some from Apple Photos and they'll appear here.</span></div>)
        : <Grid items={items} source={source} onOpen={open} onEnd={more} loading={loading} done={done} />}
    </>
  )
}
