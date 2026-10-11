// Where the library comes from. The server implementation talks to photoctl over HTTP; the
// on-device implementation (M9) runs the same SQL in SQLite WebAssembly.
import { api, qs } from '../api/client'
import type { Device, MediaDetail, Person, SearchResult } from '../api/types'

export interface SearchParams {
  q?: string; person?: string; place?: string; tag?: string; album?: string
  from?: string; to?: string; type?: 'photo' | 'video'; fav?: boolean; device?: string
  hidden?: boolean; cursor?: string | null; limit?: number
}

export interface DataSource {
  readonly kind: 'server' | 'device'
  readonly canEdit: boolean
  search(p: SearchParams): Promise<SearchResult>
  media(id: string): Promise<MediaDetail>
  people(): Promise<Person[]>
  devices(): Promise<Device[]>
  thumbUrl(id: string): string
  posterUrl(id: string): string
  compressedUrl(id: string, download?: boolean): string
}

export class ServerDataSource implements DataSource {
  readonly kind = 'server' as const
  readonly canEdit = true

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
