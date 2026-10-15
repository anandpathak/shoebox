// Twin of the row-building parts of shoebox/catalog/store.py.
import type { Db } from './dbapi'
import { sha1Hex } from './sha1'

export const MIME: Record<string, string> = {
  heic: 'image/heic', heif: 'image/heif', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', tif: 'image/tiff',
  tiff: 'image/tiff', gif: 'image/gif', dng: 'image/x-adobe-dng', cr2: 'image/x-canon-cr2', nef: 'image/x-nikon-nef',
  arw: 'image/x-sony-arw', mov: 'video/quicktime', mp4: 'video/mp4', m4v: 'video/x-m4v',
}
export const UNNAMED_PERSON = '_UNKNOWN_'
export const FACT_COLUMNS = ['sha256', 'media_type', 'raw_key', 'compressed_key', 'poster_key', 'thumb_key', 'meta_key',
  'orig_name', 'orig_ext', 'mime', 'bytes_raw', 'bytes_compressed', 'width', 'height', 'duration_s', 'taken_at',
  'taken_at_source', 'tz', 'lat', 'lon', 'place_country', 'place_region', 'place_city', 'place_locality', 'place_name',
  'camera_make', 'camera_model'] as const

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any

export const personId = (name: string) => 'p_' + sha1Hex(name.trim().toLowerCase()).slice(0, 16)

const first = (v: unknown) => (Array.isArray(v) ? v.find((x) => x) ?? null : v || null)

function place(apple: Any) {
  const p = apple.place || {}
  const addr = p.address || {}
  const names = p.names || {}
  return {
    place_country: addr.country || first(names.country), place_region: addr.state_province || first(names.state_province),
    place_city: addr.city || first(names.city), place_locality: addr.sub_locality || first(names.additional_city_info),
    place_name: p.name || first(names.area_of_interest),
  }
}

export function mediaRow(record: Any, bytesCompressed: number | null = null): Record<string, unknown> {
  const apple = record.apple || {}
  const exif = apple.exif_info || {}
  const file = record.file || {}
  const keys = record.keys || {}
  const ext = (file.orig_ext || '').toLowerCase()
  const lat = apple.latitude ?? exif.latitude ?? null
  const lon = apple.longitude ?? exif.longitude ?? null
  return {
    photo_id: record.photo_id, sha256: file.sha256,
    media_type: record.media_type || (apple.ismovie ? 'video' : 'photo'),
    raw_key: keys.raw, compressed_key: keys.compressed ?? null, poster_key: keys.poster ?? null, thumb_key: keys.thumb ?? null,
    meta_key: keys.meta, orig_name: file.orig_name ?? null, orig_ext: ext || null, mime: MIME[ext] ?? null,
    bytes_raw: file.bytes_raw ?? null, bytes_compressed: bytesCompressed, width: file.width ?? null, height: file.height ?? null,
    duration_s: exif.duration ?? null, taken_at: record.taken_at ?? null,
    taken_at_source: record.taken_at ? (record.taken_at_source || (record.source || {}).type || null) : null,
    tz: exif.tzname ?? null, lat, lon, camera_make: exif.camera_make ?? null, camera_model: exif.camera_model ?? null,
    title: apple.title ?? null, description: apple.description ?? null,
    favorite: apple.favorite ? 1 : 0, hidden: apple.hidden ? 1 : 0, ...place(apple),
  }
}

export function resolvePerson(db: Db, person: string, limit = 20): string {
  const seen: string[] = []
  let p = person
  while (!seen.includes(p) && seen.length < limit) {
    seen.push(p)
    const row = db.get<{ merged_into: string | null }>('SELECT merged_into FROM people WHERE person_id = ?', [p])
    if (!row || !row.merged_into) return p
    p = row.merged_into
  }
  return [...seen].sort()[0]
}

const LIVE = 'add_op NOT IN (SELECT add_op FROM tombstones)'

export function refreshFts(db: Db, photoId: string) {
  const m = db.get<Any>('SELECT * FROM media WHERE photo_id = ?', [photoId])
  db.run('DELETE FROM media_fts WHERE photo_id = ?', [photoId])
  if (!m || m.deleted) return
  const tags = db.all<{ tag: string }>(`SELECT DISTINCT tag FROM media_tags WHERE photo_id = ? AND ${LIVE}`, [photoId]).map((r) => r.tag)
  const albums = db.all<{ album: string }>(`SELECT DISTINCT album FROM media_albums WHERE photo_id = ? AND ${LIVE}`, [photoId]).map((r) => r.album)
  const survivors = new Set(db.all<{ person_id: string }>(`SELECT DISTINCT person_id FROM media_people WHERE photo_id = ? AND ${LIVE}`, [photoId])
    .map((r) => resolvePerson(db, r.person_id)))
  const people = [...survivors].map((p) => db.get<{ name: string | null }>('SELECT name FROM people WHERE person_id = ?', [p])?.name)
    .filter((n): n is string => !!n).sort()
  const placeText = [m.place_name, m.place_locality, m.place_city, m.place_region, m.place_country].filter(Boolean).join(' ')
  db.run('INSERT INTO media_fts (photo_id, title, description, tags, people, place, orig_name, albums) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [photoId, m.title ?? '', [m.description, m.caption, m.text_in_image].filter(Boolean).join(' '), tags.join(' '), people.join(' '), placeText, m.orig_name ?? '', albums.join(' ')])
}
