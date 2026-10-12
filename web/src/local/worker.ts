/// <reference lib="webworker" />
// On-device catalog: SQLite (WebAssembly) in a Web Worker, stored in the browser's private file system
// with the "opfs-sahpool" VFS, which works in iOS Safari 16.4+ and Android Chrome without special headers.
import sqlite3InitModule from '@sqlite.org/sqlite-wasm'
import { wrap, type Db } from './dbapi'
import { handlers } from './handlers'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Any = any
let sqlite3: Any
let pool: Any = null
let poolError: string | null = null
const open = new Map<string, { raw: Any; db: Db }>()

async function init() {
  if (sqlite3) return
  sqlite3 = await (sqlite3InitModule as unknown as (o: object) => Promise<Any>)({ print: () => {}, printErr: () => {} })
  try {
    pool = await sqlite3.installOpfsSAHPoolVfs({ name: 'photoctl-pool', directory: '/photoctl', initialCapacity: 12 })
  } catch (e) {
    pool = null  // no OPFS here: on-device mode isn't available, everything still works online
    poolError = (e as Error).message
  }
}

export interface Env {
  persistent: boolean
  unavailableReason: string | null
  db(name: string): Db
  importDb(name: string, bytes: Uint8Array): void
  exportDb(name: string): Uint8Array
  list(): string[]
  remove(name: string): void
}

const env: Env = {
  get persistent() { return pool !== null },
  get unavailableReason() { return poolError },
  db(name) {
    let h = open.get(name)
    if (!h) {
      if (!pool) throw new Error('On-device storage isn’t available in this browser')
      const raw = new pool.OpfsSAHPoolDb(`/${name}.sqlite`)
      raw.exec('PRAGMA busy_timeout = 2000')
      h = { raw, db: wrap(raw) }
      open.set(name, h)
    }
    return h.db
  },
  importDb(name, bytes) {
    if (!pool) throw new Error('On-device storage isn’t available in this browser')
    open.get(name)?.raw.close()
    open.delete(name)
    pool.importDb(`/${name}.sqlite`, bytes)
  },
  exportDb(name) {
    open.get(name)?.raw.close()
    open.delete(name)
    return pool.exportFile(`/${name}.sqlite`)
  },
  list() {
    return pool ? pool.getFileNames().filter((f: string) => f.endsWith('.sqlite')).map((f: string) => f.slice(1, -7)) : []
  },
  remove(name) {
    open.get(name)?.raw.close()
    open.delete(name)
    pool?.unlink(`/${name}.sqlite`)
  },
}

self.onmessage = async (e: MessageEvent<{ id: number; op: string; args: unknown[] }>) => {
  const { id, op, args } = e.data
  try {
    await init()
    const fn = (handlers as Record<string, (env: Env, ...a: unknown[]) => unknown>)[op]
    if (!fn) throw new Error(`unknown op ${op}`)
    const result = await fn(env, ...args)
    const transfer = result instanceof Uint8Array ? [result.buffer] : []
    ;(self as unknown as Worker).postMessage({ id, result }, transfer as Transferable[])
  } catch (err) {
    ;(self as unknown as Worker).postMessage({ id, error: (err as Error).message })
  }
}
