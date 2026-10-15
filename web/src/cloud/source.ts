// The library read from S3 with the read-only key: the catalog file is downloaded into the on-device
// SQLite (same search code as everywhere else), pictures load from signed S3 links. Nothing can be changed.
import type { Device, MediaDetail, Person, SearchResult } from '../api/types'
import type { DataSource, Editor, SearchParams } from '../data/source'
import { gunzip, local } from '../local/client'
import { downloadResumable } from '../local/download'
import { loadAccess, type ViewerAccess } from './access'
import { presign } from './sigv4'

const NAME = 'viewer'
const STATE = 'shoebox.viewerState'

export interface ViewerState { sha256: string; created_at: string; items: number; checked_at: string }

export function viewerState(): ViewerState | null {
  try { const s = localStorage.getItem(STATE); return s ? JSON.parse(s) : null } catch { return null }
}

const readOnly = () => Promise.reject(new Error("Read-only: changes need your Mac"))
const NO_EDITS: Editor = {
  patch: readOnly, addTag: readOnly, removeTag: readOnly, remove: readOnly, restore: readOnly,
  renamePerson: readOnly, mergePerson: readOnly,
}

interface Keys { thumb_key?: string | null; compressed_key?: string | null; poster_key?: string | null; orig_name?: string | null }

export class CloudDataSource implements DataSource {
  readonly kind = 'cloud' as const
  readonly canEdit = false
  readonly edit = NO_EDITS
  private keys = new Map<string, Keys>()
  private access: ViewerAccess
  constructor(access: ViewerAccess) { this.access = access }

  static async create(): Promise<CloudDataSource | null> {
    const a = await loadAccess()
    return a ? new CloudDataSource(a) : null
  }

  url(key: string, query?: Record<string, string>): string {
    const a = this.access
    return presign({ keyId: a.key_id, secret: a.secret, region: a.region },
      { bucket: a.bucket, key, endpoint: a.endpoint, pathStyle: a.path_style }, { query })
  }

  /** Download the library if the Mac published a newer one. Returns the state, or throws when unreachable. */
  async refresh(onProgress?: (got: number, total: number) => void): Promise<ViewerState> {
    const res = await fetch(presign({ keyId: this.access.key_id, secret: this.access.secret, region: this.access.region },
      { bucket: this.access.bucket, key: this.access.head, endpoint: this.access.endpoint, pathStyle: this.access.path_style },
      { when: new Date() }), { cache: 'no-store' })
    if (!res.ok) throw new Error(res.status === 403 ? 'The read-only key was refused (rotated?). Set it up again from your Mac.'
      : `Couldn't read the library (${res.status})`)
    const head = await res.json() as { sha256: string; created_at: string; items: number }
    const cur = viewerState()
    const have = await local.call<boolean>('hasCatalog', NAME).catch(() => false)
    if (!cur || cur.sha256 !== head.sha256 || !have) {
      const { bytes } = await downloadResumable(this.url(this.access.viewer, { 'x-sha': head.sha256.slice(0, 12) }), onProgress)
      await local.call('importCatalog', NAME, await gunzip(bytes.buffer as ArrayBuffer))
    }
    const st = { sha256: head.sha256, created_at: head.created_at, items: head.items, checked_at: new Date().toISOString() }
    try { localStorage.setItem(STATE, JSON.stringify(st)) } catch { /* fine */ }
    return st
  }

  private remember<T extends { photo_id: string } & Keys>(items: T[]): T[] {
    for (const i of items) this.keys.set(i.photo_id, { ...this.keys.get(i.photo_id), ...i })
    return items
  }

  async search(p: SearchParams): Promise<SearchResult> {
    const { smart: _smart, ...rest } = p                       // plain-English ranking needs the Mac
    const r = await local.call<SearchResult>('searchMany', [NAME], rest)
    this.remember(r.items as never[])
    return r
  }
  async media(id: string): Promise<MediaDetail> {
    const m = await local.call<MediaDetail>('media', [NAME], id)
    this.remember([m as never])
    return m
  }
  people() { return local.call<Person[]>('people', [NAME]) }
  devices() { return local.call<Device[]>('devices', [NAME]) }

  thumbUrl(id: string) {
    const k = this.keys.get(id)?.thumb_key
    return k ? this.url(k) : ''
  }
  posterUrl(id: string) {
    const k = this.keys.get(id)?.poster_key ?? this.keys.get(id)?.thumb_key
    return k ? this.url(k) : ''
  }
  compressedUrl(id: string, download = false) {
    const k = this.keys.get(id)
    if (!k?.compressed_key) return ''
    const name = (k.orig_name ?? id).replace(/\.[^.]+$/, '') + (k.compressed_key.match(/\.[^.]+$/)?.[0] ?? '')
    return this.url(k.compressed_key, download ? { 'response-content-disposition': `attachment; filename="${name.replace(/"/g, '')}"` } : undefined)
  }
}
