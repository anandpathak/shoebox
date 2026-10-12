// Operations the on-device worker offers. Each gets the storage env first.
import { applyOps, type Op } from './apply'
import { validate } from './ops'
import { mediaDetail, search, type SearchOptions } from './query'
import { QUERIES } from './sql'
import type { Env } from './worker'

export const handlers = {
  status(env: Env) {
    return { persistent: env.persistent, reason: env.unavailableReason, databases: env.list() }
  },
  importCatalog(env: Env, name: string, bytes: Uint8Array) {
    env.importDb(name, bytes)
    const db = env.db(name)
    const ok = db.get<{ integrity_check: string }>('SELECT integrity_check FROM pragma_integrity_check')
    const items = db.get<{ n: number }>('SELECT COUNT(*) AS n FROM media WHERE deleted = 0')
    if (ok?.integrity_check !== 'ok') throw new Error('The downloaded catalog is damaged')
    return { items: items?.n ?? 0 }
  },
  query(env: Env, name: string, sql: string, params?: Record<string, unknown>) {
    return env.db(name).all(sql, params)
  },
  search(env: Env, name: string, o: SearchOptions) {
    return search(env.db(name), o)
  },
  media(env: Env, name: string, id: string) {
    const d = mediaDetail(env.db(name), id)
    if (!d) throw new Error('No such item on this device')
    return d
  },
  people(env: Env, name: string) {
    return env.db(name).all(QUERIES.people)
  },
  devices(env: Env, name: string) {
    return env.db(name).all(QUERIES.devices)
  },
  applyOps(env: Env, name: string, ops: Op[]) {
    return applyOps(env.db(name), ops, validate)
  },
  remove(env: Env, name: string) {
    env.remove(name)
    return true
  },
  exportCatalog(env: Env, name: string) {
    return env.exportDb(name)
  },
}
