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
