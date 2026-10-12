// TypeScript twin of photoctl/catalog/query.py. Both must pass spec/query_vectors.json and return the
// same rows for the same catalog (search parity test).
import type { MediaItem, ParsedQuery, SearchResult } from '../api/types'
import type { Db } from './dbapi'
import { QUERIES } from './sql'
import { resolvePerson } from './store'

const MONTH_NAMES: string[][] = [['january', 'jan'], ['february', 'feb'], ['march', 'mar'], ['april', 'apr'], ['may'],
  ['june', 'jun'], ['july', 'jul'], ['august', 'aug'], ['september', 'sep', 'sept'], ['october', 'oct'],
  ['november', 'nov'], ['december', 'dec']]
const MONTHS = new Map(MONTH_NAMES.flatMap((names, i) => names.map((n) => [n, i + 1] as [string, number])))
const TYPES: Record<string, 'photo' | 'video'> = { photo: 'photo', photos: 'photo', video: 'video', videos: 'video' }
const YEAR = /^(19|20)\d\d$/
export const PAGE = 60

/** Python's `[^\W_]+` with re.UNICODE: runs of letters and digits. */
export function tokens(text: string | null | undefined): string[] {
  return (text ?? '').toLowerCase().match(/[\p{L}\p{N}\p{M}]+/gu) ?? []
}

const pad = (n: number, w = 2) => String(n).padStart(w, '0')

export function parseQuery(text: string | null | undefined): ParsedQuery {
  const out: ParsedQuery = { terms: [], date_from: null, date_to: null, month: null, media_type: null }
  let year: number | null = null
  let month: number | null = null
  for (const tok of tokens(text)) {
    if (YEAR.test(tok) && year === null) year = Number(tok)
    else if (MONTHS.has(tok) && month === null) month = MONTHS.get(tok)!
    else if (tok in TYPES && out.media_type === null) out.media_type = TYPES[tok]
    else out.terms.push(tok)
  }
  if (year && month) {
    out.date_from = `${pad(year, 4)}-${pad(month)}-01`
    out.date_to = `${pad(year + (month === 12 ? 1 : 0), 4)}-${pad((month % 12) + 1)}-01`
  } else if (year) {
    out.date_from = `${pad(year, 4)}-01-01`
    out.date_to = `${pad(year + 1, 4)}-01-01`
  } else if (month) out.month = pad(month)
  return out
}

const phrase = (tok: string, prefix: boolean) => `"${tok.replaceAll('"', '""')}"${prefix ? '*' : ''}`

export function buildFts(terms: string[] = [], f: { person?: string | null; place?: string | null; tag?: string | null; album?: string | null } = {}): string | null {
  const parts = terms.map((t) => phrase(t, true))
  for (const [column, value, prefix] of [['people', f.person, false], ['place', f.place, true], ['tags', f.tag, false],
                                          ['albums', f.album, false]] as const) {
    for (const t of tokens(value)) parts.push(`${column} : ${phrase(t, prefix)}`)
  }
  return parts.length ? parts.join(' AND ') : null
}

const b64url = (s: string) => btoa(unescape(encodeURIComponent(s))).replaceAll('+', '-').replaceAll('/', '_')
const unb64url = (s: string) => decodeURIComponent(escape(atob(s.replaceAll('-', '+').replaceAll('_', '/'))))

export function encodeCursor(row: { taken_at: string | null; photo_id: string }): string {
  return b64url(JSON.stringify([row.taken_at ?? '', row.photo_id]))
}

export function decodeCursor(c: string | null | undefined): [string | null, string | null] {
  if (!c) return [null, null]
  const [t, p] = JSON.parse(unb64url(c))
  return [t, p]
}

export interface SearchOptions {
  q?: string; person?: string; place?: string; tag?: string; album?: string; from?: string; to?: string
  type?: 'photo' | 'video'; fav?: boolean; device?: string; hidden?: boolean; cursor?: string | null; limit?: number
}

export function search(db: Db, o: SearchOptions): SearchResult {
  const parsed = parseQuery(o.q)
  const limit = o.limit ?? PAGE
  const [taken, pid] = decodeCursor(o.cursor)
  const params = {
    fts: buildFts(parsed.terms, o), date_from: o.from || parsed.date_from, date_to: o.to || parsed.date_to,
    month: o.from || o.to ? null : parsed.month, media_type: o.type || parsed.media_type,
    favorite: o.fav === undefined || o.fav === null ? null : Number(o.fav), device: o.device ?? null,
    include_hidden: Number(!!o.hidden), cursor_taken: taken, cursor_id: pid, limit: limit + 1,
  }
  const rows = db.all<MediaItem & { devices: string | string[] | null }>(QUERIES.search, params)
  const more = rows.length > limit
  const items = rows.slice(0, limit).map((r) => ({ ...r, devices: typeof r.devices === 'string' && r.devices ? r.devices.split(',').sort() : [] }))
  return { items: items as MediaItem[], next: more && items.length ? encodeCursor(items[items.length - 1]) : null, query: parsed }
}

export function mediaDetail(db: Db, photoId: string) {
  const row = db.get<Record<string, unknown>>(QUERIES.media_detail, { photo_id: photoId })
  if (!row) return null
  const d: Record<string, unknown> = { ...row }
  for (const k of ['tags', 'albums', 'sources', 'people']) {
    d[k] = JSON.parse((d[`${k}_json`] as string) || '[]')
    delete d[`${k}_json`]
  }
  const people = new Map<string, { person_id: string; name: string | null; add_ops: string[] }>()
  for (const p of d.people as { person_id: string; add_op: string }[]) {
    const s = resolvePerson(db, p.person_id)
    const name = db.get<{ name: string | null }>('SELECT name FROM people WHERE person_id = ?', [s])?.name ?? null
    const cur = people.get(s) ?? { person_id: s, name, add_ops: [] }
    cur.add_ops.push(p.add_op)
    people.set(s, cur)
  }
  d.people = [...people.values()].sort((a, b) => (a.name ?? '').localeCompare(b.name ?? ''))
  delete d.field_hlc
  return d
}

/** Merge results from several catalogs: one entry per photo (devices unioned), newest first, then page. */
export function mergeResults(results: { name: string; items: MediaItem[] }[], limit: number, q?: string): SearchResult {
  const merged = new Map<string, MediaItem & { catalogs: string[] }>()
  for (const { name, items } of results) {
    for (const it of items) {
      const cur = merged.get(it.photo_id)
      if (cur) { cur.devices = [...new Set([...cur.devices, ...it.devices])].sort(); cur.catalogs.push(name) }
      else merged.set(it.photo_id, { ...it, catalogs: [name] })
    }
  }
  const key = (i: MediaItem) => `${i.taken_at ?? ''}\u0000${i.photo_id}`
  const all = [...merged.values()].sort((a, b) => (key(a) < key(b) ? 1 : key(a) > key(b) ? -1 : 0))
  const items = all.slice(0, limit)
  return { items, next: all.length > limit && items.length ? encodeCursor(items[items.length - 1]) : null, query: parseQuery(q) }
}
