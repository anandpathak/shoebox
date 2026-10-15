// Uploads waiting for the Mac, kept in IndexedDB so they survive the tab closing or the phone going offline
// (LOCAL_APP_PLAN.md §9.3). Each entry holds the file itself; it's removed once the server has it.
import * as tus from 'tus-js-client'

export interface Queued {
  id: string; name: string; type: string; size: number; lastModified: number; deviceId: string; addedAt: number
  blob: Blob
}
export type Progress = Record<string, { sent: number; status: 'waiting' | 'uploading' | 'done' | 'error'; error?: string }>

const DB = 'shoebox'
const STORE = 'uploads'

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'id' })
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const r = fn(db.transaction(STORE, mode).objectStore(STORE))
    r.onsuccess = () => resolve(r.result)
    r.onerror = () => reject(r.error)
  })
}

export async function enqueue(files: File[], deviceId: string): Promise<Queued[]> {
  const items = files.map((f) => ({
    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`, name: f.name, type: f.type, size: f.size,
    lastModified: f.lastModified, deviceId, addedAt: Date.now(), blob: f as Blob,
  }))
  for (const it of items) await tx('readwrite', (s) => s.put(it))
  notify()
  return items
}

export const list = () => tx<Queued[]>('readonly', (s) => s.getAll() as IDBRequest<Queued[]>)
const remove = (id: string) => tx('readwrite', (s) => s.delete(id))

// --- one queue runner per page; screens subscribe for progress
let running = false
let progress: Progress = {}
const listeners = new Set<(p: Progress) => void>()
function notify() { listeners.forEach((l) => l({ ...progress })) }
export function subscribe(fn: (p: Progress) => void): () => void {
  listeners.add(fn)
  fn({ ...progress })
  return () => { listeners.delete(fn) }
}

function upload(item: Queued): Promise<void> {
  return new Promise((resolve, reject) => {
    const up = new tus.Upload(item.blob, {
      endpoint: '/api/uploads/files',
      chunkSize: 8 * 1024 * 1024,
      retryDelays: [0, 1000, 3000, 5000],
      fingerprint: async () => `shoebox-${item.id}`,         // resume the same queued file after a reload
      removeFingerprintOnSuccess: true,
      metadata: { filename: item.name, filetype: item.type, device_id: item.deviceId, lastModified: String(item.lastModified) },
      onProgress: (sent) => { progress[item.id] = { sent, status: 'uploading' }; notify() },
      onSuccess: () => resolve(),
      onError: (e) => reject(e),
    })
    up.findPreviousUploads().then((prev) => { if (prev.length) up.resumeFromPreviousUpload(prev[0]); up.start() })
  })
}

/** Upload everything queued, oldest first. Stops at the first network failure and tries again later. */
export async function processQueue(): Promise<number> {
  if (running) return 0
  running = true
  let done = 0
  try {
    const items = (await list()).sort((a, b) => a.addedAt - b.addedAt)
    for (const it of items) if (!progress[it.id]) progress[it.id] = { sent: 0, status: 'waiting' }
    notify()
    for (const it of items) {
      try {
        await upload(it)
        await remove(it.id)
        progress[it.id] = { sent: it.size, status: 'done' }
        done++
      } catch (e) {
        progress[it.id] = { sent: progress[it.id]?.sent ?? 0, status: 'waiting', error: (e as Error).message }
        notify()
        break                                                  // probably offline: keep the rest queued
      }
      notify()
    }
  } finally { running = false }
  return done
}
