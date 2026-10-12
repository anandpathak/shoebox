// The ledger and backup settings, kept in IndexedDB on the phone (one record each). A lost ledger only
// costs re-hashing: the Mac's /api/backup/check still stops anything being sent twice.
import { emptyLedger } from './ledger'
import { DEFAULT_SETTINGS, type Ledger, type Settings } from './types'

const DB = 'photoctl-backup'
const STORE = 'kv'

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function get<T>(key: string): Promise<T | undefined> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const r = db.transaction(STORE).objectStore(STORE).get(key)
    r.onsuccess = () => resolve(r.result as T | undefined)
    r.onerror = () => reject(r.error)
  })
}

async function put(key: string, value: unknown): Promise<void> {
  const db = await open()
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, 'readwrite')
    t.objectStore(STORE).put(value, key)
    t.oncomplete = () => resolve()
    t.onerror = () => reject(t.error)
  })
}

export const loadLedger = async (): Promise<Ledger> => (await get<Ledger>('ledger')) ?? emptyLedger()
export const saveLedger = (l: Ledger) => put('ledger', l)
export const loadSettings = async (): Promise<Settings> => ({ ...DEFAULT_SETTINGS, ...(await get<Partial<Settings>>('settings')) })
export const saveSettings = (s: Settings) => put('settings', s)
