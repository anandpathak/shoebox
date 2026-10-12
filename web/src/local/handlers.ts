// Operations the on-device worker offers. Each gets the storage env first.
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
  remove(env: Env, name: string) {
    env.remove(name)
    return true
  },
  exportCatalog(env: Env, name: string) {
    return env.exportDb(name)
  },
}
