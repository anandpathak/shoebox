// Response shapes of the photoctl API (server/routes/*). Request bodies come from schema.d.ts.

export type MediaType = 'photo' | 'video'

export interface MediaItem {
  photo_id: string
  media_type: MediaType
  taken_at: string | null
  title: string | null
  favorite: number
  hidden: number
  duration_s: number | null
  width: number | null
  height: number | null
  place_name: string | null
  place_city: string | null
  place_region: string | null
  place_country: string | null
  thumb_key: string | null
  compressed_key: string | null
  poster_key: string | null
  bytes_raw: number | null
  bytes_compressed: number | null
  devices: string[]
  catalogs?: string[]
}

export interface ParsedQuery {
  terms: string[]
  date_from: string | null
  date_to: string | null
  month: string | null
  media_type: MediaType | null
}

export interface SearchResult {
  items: MediaItem[]
  next: string | null
  query?: ParsedQuery
  mode?: 'smart' | 'keywords' | 'filters'   // smart = ranked by meaning (plain-English search on the Mac)
  people?: string[]                         // names in the query, used as filters
}

export interface Source { device_id: string; name: string | null; platform: string | null; source_ref: string }
export interface PersonRef { person_id: string; name: string | null; add_ops: string[] }

export interface MediaDetail extends Omit<MediaItem, 'devices'> {
  sha256: string
  raw_key: string
  orig_name: string | null
  mime: string | null
  description: string | null
  caption?: string | null          // generated on the Mac (Florence-2) unless the user wrote one
  text_in_image?: string | null    // words read from the picture (signs, receipts, documents)
  deleted: number
  lat: number | null
  lon: number | null
  camera_make: string | null
  camera_model: string | null
  tz: string | null
  taken_at_source: string | null
  tags: string[]
  albums: string[]
  sources: Source[]
  people: PersonRef[]
}

export interface Person { person_id: string; name: string | null; photos: number }
export interface Device {
  device_id: string; name: string | null; platform: string | null
  first_seen_hlc: string | null; last_seen_hlc: string | null; items: number
}

export interface SyncStatus {
  running: boolean; last_run: string | null; last_ok: string | null; error: string | null
  last_result: { pushed_ops: number; applied_ops: number; pulled_batches: number; pushed_batches: number } | null
  pending_ops: number; s3: boolean; interval: number
}

export interface Health {
  ok: boolean; user: string; via: 'token' | 'cookie' | 'tailscale' | 'paired'
  device: { device_id: string; name: string; platform: string }; s3: boolean
}

export interface OriginalStatus {
  photo_id: string; status: 'in_progress' | 'ready' | 'available' | 'expired' | 'not_requested'
  tier: string; days: number; requested_at: string; ready_until: string | null; eta_hours: number; url?: string
}

export interface CatalogManifest {
  device_id: string; name: string; platform: string; item_count: number; contributed_count?: number
  created_at: string; bytes?: number; checksum?: string; kind: 'library' | 'published'
}

export interface BackupList {
  last: { created_at: string; prefix: string; bucket: string } | null
  backups: { created_at: string; prefix: string; bytes: number; files: string[] }[]
}
