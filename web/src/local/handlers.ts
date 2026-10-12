// Operations the on-device worker offers. Each gets the storage env first.
import { applyOps, type Op } from './apply'
import { HLC } from './hlc'
import { makeOp, validate } from './ops'
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
    const db = env.db(name)
    const n = applyOps(db, ops, validate)
    const top = ops.reduce((m, o) => (o.hlc > m ? o.hlc : m), getMeta(db, 'hlc_seen') ?? '')
    if (top) setMeta(db, 'hlc_seen', top)
    return n
  },
  /** Make edits on this device: ops with this device's clock, applied here and queued in the outbox. */
  emit(env: Env, name: string, deviceId: string, items: [string, Record<string, unknown>][]) {
    const db = env.db(name)
    const clock = new HLC(deviceId, getMeta(db, `hlc_last:${deviceId}`))
    const seen = getMeta(db, 'hlc_seen')
    if (seen) clock.observe(seen)
    const ops = items.map(([kind, body]) => makeOp(clock, kind, body))
    db.tx(() => {
      applyOps(db, ops)
      for (const op of ops) db.run('INSERT OR IGNORE INTO outbox (op_id) VALUES (?)', [op.op_id])
      setMeta(db, `hlc_last:${deviceId}`, clock.last)
    })
    return ops
  },
  outbox(env: Env, name: string, limit = 500) {
    const rows = env.db(name).all<Op & { body: string }>(
      'SELECT o.* FROM outbox x JOIN ops o ON o.op_id = x.op_id ORDER BY o.hlc LIMIT ?', [limit])
    return rows.map((r) => ({ op_id: r.op_id, hlc: r.hlc, device_id: r.device_id, kind: r.kind, entity: r.entity,
                              body: JSON.parse(r.body as unknown as string) }))
  },
  outboxCount(env: Env, name: string) {
    return env.db(name).get<{ n: number }>('SELECT COUNT(*) AS n FROM outbox')?.n ?? 0
  },
  clearOutbox(env: Env, name: string, ids: string[]) {
    const db = env.db(name)
    db.tx(() => { for (const id of ids) db.run('DELETE FROM outbox WHERE op_id = ?', [id]) })
    return ids.length
  },
  liveAdds(env: Env, name: string, table: string, col: string, photoId: string, value: string) {
    if (!['media_tags', 'media_albums'].includes(table) || !['tag', 'album'].includes(col)) throw new Error('bad table')
    return env.db(name).all<{ add_op: string }>(
      `SELECT add_op FROM ${table} WHERE photo_id = ? AND ${col} = ? AND add_op NOT IN (SELECT add_op FROM tombstones)`,
      [photoId, value]).map((r) => r.add_op)
  },
  remove(env: Env, name: string) {
    env.remove(name)
    return true
  },
  exportCatalog(env: Env, name: string) {
    return env.exportDb(name)
  },
}

function getMeta(db: import('./dbapi').Db, key: string): string | null {
  return db.get<{ value: string }>('SELECT value FROM meta WHERE key = ?', [key])?.value ?? null
}

function setMeta(db: import('./dbapi').Db, key: string, value: string) {
  db.run('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', [key, value])
}
