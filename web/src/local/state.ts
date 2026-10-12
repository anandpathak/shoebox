// What's downloaded on this device (small bookkeeping in localStorage; the catalog itself is in OPFS).
export interface LocalState {
  enabled: boolean
  downloadedAt: string | null
  cursor: number          // server op sequence the catalog includes (X-Ops-Cursor)
  epoch: string | null    // server catalog epoch (X-Catalog-Epoch)
  items: number
  refreshedAt: string | null
  publishedAt?: string | null
  open?: string[]          // on-device catalogs to search together (default: just the library)
  preferDevice?: boolean   // search the on-device catalogs even while the Mac is reachable
  devices?: Record<string, { name: string; platform: string; items: number; downloadedAt: string; createdAt: string }>
}

const KEY = 'photoctl.local'
const EMPTY: LocalState = { enabled: false, downloadedAt: null, cursor: 0, epoch: null, items: 0, refreshedAt: null }
const listeners = new Set<(s: LocalState) => void>()

export function getLocal(): LocalState {
  try { return { ...EMPTY, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') } } catch { return { ...EMPTY } }
}

export function setLocal(patch: Partial<LocalState>): LocalState {
  const next = { ...getLocal(), ...patch }
  try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* ignore */ }
  listeners.forEach((l) => l(next))
  return next
}

export function clearLocal() { setLocal(EMPTY) }

export function onLocal(fn: (s: LocalState) => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}
