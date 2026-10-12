// Images for offline use (LOCAL_APP_PLAN.md §10.4): thumbnail packs by year, and compressed copies saved per item.
// Both live in Cache Storage, which the service worker serves from when the Mac can't be reached.
import { api, qs } from '../api/client'
import type { SearchResult } from '../api/types'

export const THUMBS = 'thumbs-v1'
export const SAVED = 'saved-v1'

/** Fetch every thumbnail taken in `year` into the thumbnail cache. */
export async function downloadThumbs(year: string, onProgress: (done: number, total: number) => void): Promise<number> {
  const ids: string[] = []
  let cursor: string | null = null
  do {
    const page: SearchResult = await api(`/api/search${qs({ from: `${year}-01-01`, to: `${Number(year) + 1}-01-01`, limit: 500, cursor })}`)
    ids.push(...page.items.filter((i) => i.thumb_key).map((i) => i.photo_id))
    cursor = page.next
  } while (cursor)
  const cache = await caches.open(THUMBS)
  let done = 0
  const queue = [...ids]
  const worker = async () => {
    for (let id = queue.shift(); id; id = queue.shift()) {
      const url = `/api/media/${encodeURIComponent(id)}/thumb`
      if (!(await cache.match(url))) {
        const res = await fetch(url, { credentials: 'same-origin' })
        if (res.ok) await cache.put(url, res)
      }
      onProgress(++done, ids.length)
    }
  }
  await Promise.all(Array.from({ length: 6 }, worker))
  return ids.length
}

const savedKey = (id: string) => `/api/media/${encodeURIComponent(id)}/compressed`

export async function saveOffline(id: string): Promise<void> {
  const res = await fetch(savedKey(id), { credentials: 'same-origin' })
  if (!res.ok) throw new Error(`Couldn't save it (${res.status})`)
  await (await caches.open(SAVED)).put(savedKey(id), res)
}

export async function unsaveOffline(id: string): Promise<void> {
  await (await caches.open(SAVED)).delete(savedKey(id))
}

export async function isSaved(id: string): Promise<boolean> {
  return !!(await (await caches.open(SAVED)).match(savedKey(id)))
}

export async function savedCount(): Promise<number> {
  return (await (await caches.open(SAVED)).keys()).length
}

export async function clearOfflineImages(): Promise<void> {
  await Promise.all([caches.delete(THUMBS), caches.delete(SAVED)])
}
