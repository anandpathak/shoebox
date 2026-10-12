// Where the library comes from. The server implementation talks to photoctl over HTTP; the
// on-device implementation (M9) runs the same SQL in SQLite WebAssembly.
import { api, qs } from '../api/client'
import type { Device, MediaDetail, Person, SearchResult } from '../api/types'

export interface SearchParams {
  q?: string; person?: string; place?: string; tag?: string; album?: string
  from?: string; to?: string; type?: 'photo' | 'video'; fav?: boolean; device?: string
  hidden?: boolean; cursor?: string | null; limit?: number
}

export interface MediaPatch { title?: string | null; description?: string | null; favorite?: boolean; hidden?: boolean }

export interface Editor {
  patch(id: string, patch: MediaPatch): Promise<MediaDetail>
  addTag(id: string, tag: string): Promise<MediaDetail>
  removeTag(id: string, tag: string): Promise<MediaDetail>
  remove(id: string): Promise<void>
  restore(id: string): Promise<MediaDetail>
  renamePerson(personId: string, name: string): Promise<void>
  mergePerson(personId: string, into: string): Promise<void>
}

export interface DataSource {
  readonly kind: 'server' | 'device'
  readonly canEdit: boolean
  readonly edit: Editor
  search(p: SearchParams): Promise<SearchResult>
  media(id: string): Promise<MediaDetail>
  people(): Promise<Person[]>
  devices(): Promise<Device[]>
  thumbUrl(id: string): string
  posterUrl(id: string): string
  compressedUrl(id: string, download?: boolean): string
}

const enc = encodeURIComponent

export class ServerDataSource implements DataSource {
  readonly kind = 'server' as const
  readonly canEdit = true
  readonly edit: Editor = {
    patch: (id, patch) => api<MediaDetail>(`/api/media/${enc(id)}`, { method: 'PATCH', json: patch }),
    addTag: (id, tag) => api<MediaDetail>(`/api/media/${enc(id)}/tags`, { method: 'POST', json: { value: tag } }),
    removeTag: (id, tag) => api<MediaDetail>(`/api/media/${enc(id)}/tags/${enc(tag)}`, { method: 'DELETE' }),
    remove: async (id) => { await api(`/api/media/${enc(id)}/delete`, { method: 'POST' }) },
    restore: (id) => api<MediaDetail>(`/api/media/${enc(id)}/restore`, { method: 'POST' }),
    renamePerson: async (pid, name) => { await api(`/api/people/${enc(pid)}`, { method: 'PATCH', json: { name } }) },
    mergePerson: async (pid, into) => { await api(`/api/people/${enc(pid)}/merge`, { method: 'POST', json: { into } }) },
  }

  search(p: SearchParams) {
    return api<SearchResult>(`/api/search${qs({ ...p })}`)
  }
  media(id: string) {
    return api<MediaDetail>(`/api/media/${encodeURIComponent(id)}`)
  }
  people() {
    return api<Person[]>('/api/people')
  }
  devices() {
    return api<Device[]>('/api/devices')
  }
  thumbUrl(id: string) {
    return `/api/media/${encodeURIComponent(id)}/thumb`
  }
  posterUrl(id: string) {
    return `/api/media/${encodeURIComponent(id)}/poster`
  }
  compressedUrl(id: string, download = false) {
    return `/api/media/${encodeURIComponent(id)}/compressed${download ? '?download=true' : ''}`
  }
}

/** The library on this device (on-device catalog in OPFS). Edits become ops queued for the Mac. */
export class LocalDataSource implements DataSource {
  readonly kind = 'device' as const
  readonly canEdit = true
  readonly edit: Editor
  private server = new ServerDataSource()
  private name: string
  constructor(name = 'library') {
    this.name = name
    const emit = async (items: [string, Record<string, unknown>][]) => {
      const { getDevice } = await import('../lib/device')
      return this.call('emit', getDevice().device_id, items)
    }
    const norm = (s: string) => s.split(/\s+/).filter(Boolean).join(' ')
    this.edit = {
      patch: async (id, p) => {
        await emit(Object.entries(p).map(([field, value]) => ['media.set', { photo_id: id, field, value: value ?? null }]))
        return this.media(id)
      },
      addTag: async (id, tag) => { await emit([['tag.add', { photo_id: id, tag }]]); return this.media(id) },
      removeTag: async (id, tag) => {
        const seen = await this.call<string[]>('liveAdds', 'media_tags', 'tag', id, norm(tag).toLowerCase())
        if (seen.length) await emit([['tag.remove', { photo_id: id, tag, observed_add_ops: seen }]])
        return this.media(id)
      },
      remove: async (id) => { await emit([['media.delete', { photo_id: id }]]) },
      restore: async (id) => { await emit([['media.restore', { photo_id: id }]]); return this.media(id) },
      renamePerson: async (pid, name) => { await emit([['person.set', { person_id: pid, field: 'name', value: name }]]) },
      mergePerson: async (pid, into) => { await emit([['person.merge', { from: pid, into }]]) },
    }
  }
  private call<T>(op: string, ...args: unknown[]) {
    return import('../local/client').then(({ local }) => local.call<T>(op, this.name, ...args))
  }
  search(p: SearchParams) { return this.call<SearchResult>('search', p) }
  media(id: string) { return this.call<MediaDetail>('media', id) }
  people() { return this.call<Person[]>('people') }
  devices() { return this.call<Device[]>('devices') }
  // Files: same URLs; thumbnails come from the service-worker cache when offline.
  thumbUrl(id: string) { return this.server.thumbUrl(id) }
  posterUrl(id: string) { return this.server.posterUrl(id) }
  compressedUrl(id: string, download = false) { return this.server.compressedUrl(id, download) }
}
