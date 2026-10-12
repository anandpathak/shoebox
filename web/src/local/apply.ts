// TypeScript twin of photoctl/catalog/apply.py — the same rules, checked by spec/sync_vectors.
//  * Scalar fields are last-writer-wins by HLC; field_hlc stores "<priority>|<hlc>" (0 = import, 1 = edit).
//  * Tags, albums and people-on-photo are observed-remove sets (a remove cancels only the adds it saw).
//  * Each op applies once (ops.op_id); edits for photos that haven't arrived wait in deferred_ops.
import type { Db } from './dbapi'
import { FACT_COLUMNS, mediaRow, personId, refreshFts, UNNAMED_PERSON } from './store'

export interface Op { op_id: string; hlc: string; device_id: string; kind: string; entity: string; body: Record<string, any> } // eslint-disable-line @typescript-eslint/no-explicit-any

const EDITABLE = ['title', 'description', 'favorite', 'hidden']
const IMPORT = '0', EDIT = '1'
const ver = (p: string, hlc: string) => `${p}|${hlc}`
const norm = (s: string) => s.split(/\s+/).filter(Boolean).join(' ')

/** Python json.dumps(..., sort_keys=True, separators=(",", ":")) for plain JSON values. */
export function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`
  if (v && typeof v === 'object') {
    return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical((v as Record<string, unknown>)[k])}`).join(',')}}`
  }
  return JSON.stringify(v)
}

class Ctx {
  touched = new Set<string>()
  people = new Set<string>()
  db: Db
  constructor(db: Db) { this.db = db }
}

function lwwRow(ctx: Ctx, table: string, keyCol: string, key: string, values: Record<string, unknown>, version: string) {
  const row = ctx.db.get<{ field_hlc: string | null }>(`SELECT field_hlc FROM ${table} WHERE ${keyCol} = ?`, [key])
  let fh: Record<string, string> = {}
  if (!row) ctx.db.run(`INSERT INTO ${table} (${keyCol}) VALUES (?)`, [key])
  else fh = row.field_hlc ? JSON.parse(row.field_hlc) : {}
  const changed = Object.entries(values).filter(([f]) => version > (fh[f] ?? ''))
  if (!changed.length) return
  for (const [f] of changed) fh[f] = version
  ctx.db.run(`UPDATE ${table} SET ${changed.map(([f]) => `${f} = ?`).join(', ')}, field_hlc = ? WHERE ${keyCol} = ?`,
    [...changed.map(([, v]) => v), canonical(fh), key])
}

const mediaExists = (ctx: Ctx, pid: string) => !!ctx.db.get('SELECT 1 AS x FROM media WHERE photo_id = ?', [pid])
const defer = (ctx: Ctx, op: Op, pid: string) => ctx.db.run('INSERT OR IGNORE INTO deferred_ops (op_id, photo_id) VALUES (?, ?)', [op.op_id, pid])

function mediaUpsert(ctx: Ctx, op: Op) {
  const { body, hlc } = op
  const record = body.record
  const row = mediaRow(record, body.bytes_compressed ?? null)
  const pid = row.photo_id as string
  const existing = ctx.db.get('SELECT created_hlc FROM media WHERE photo_id = ?', [pid])
  if (!existing) {
    ctx.db.run('INSERT INTO media (photo_id, sha256, media_type, raw_key, meta_key, created_hlc, updated_hlc) VALUES (?, ?, ?, ?, ?, ?, ?)',
      [pid, row.sha256, row.media_type, row.raw_key, row.meta_key, hlc, hlc])
  } else {
    ctx.db.run('UPDATE media SET created_hlc = MIN(created_hlc, ?), updated_hlc = MAX(updated_hlc, ?) WHERE photo_id = ?', [hlc, hlc, pid])
  }
  const facts = Object.fromEntries(FACT_COLUMNS.filter((c) => row[c] !== null && row[c] !== undefined).map((c) => [c, row[c]]))
  lwwRow(ctx, 'media', 'photo_id', pid, facts, ver(EDIT, hlc))
  const imported = Object.fromEntries(EDITABLE.filter((f) => row[f] !== null && row[f] !== undefined).map((f) => [f, row[f]]))
  lwwRow(ctx, 'media', 'photo_id', pid, imported, ver(IMPORT, hlc))

  const apple = record.apple || {}
  for (const [source, values] of [['apple-keyword', apple.keywords], ['apple-label', apple.labels]] as const) {
    const tags = new Set<string>((values || []).filter((t: string) => t && norm(t)).map((t: string) => norm(t).toLowerCase()))
    for (const tag of tags) {
      ctx.db.run('INSERT OR IGNORE INTO media_tags (photo_id, tag, source, add_op) VALUES (?, ?, ?, ?)', [pid, tag, source, `import:${pid}:${source}:${tag}`])
    }
  }
  for (const album of new Set<string>((apple.albums || []).filter((a: string) => a && norm(a)).map(norm))) {
    ctx.db.run('INSERT OR IGNORE INTO media_albums (photo_id, album, add_op) VALUES (?, ?, ?)', [pid, album, `import:${pid}:album:${album}`])
  }
  for (const name of new Set<string>((apple.persons || []).filter((n: string) => n && norm(n)).map(norm))) {
    if (name === UNNAMED_PERSON) continue
    const per = personId(name)
    lwwRow(ctx, 'people', 'person_id', per, { name }, ver(IMPORT, hlc))
    ctx.db.run('INSERT OR IGNORE INTO media_people (photo_id, person_id, source, add_op) VALUES (?, ?, ?, ?)', [pid, per, 'apple', `import:${pid}:person:${per}`])
  }
  ctx.touched.add(pid)
  replayDeferred(ctx, pid)
}

function replayDeferred(ctx: Ctx, pid: string) {
  const rows = ctx.db.all<Op & { body: string }>(
    'SELECT o.* FROM deferred_ops d JOIN ops o ON o.op_id = d.op_id WHERE d.photo_id = ? ORDER BY o.hlc', [pid])
  ctx.db.run('DELETE FROM deferred_ops WHERE photo_id = ?', [pid])
  for (const r of rows) dispatch(ctx, { ...r, body: JSON.parse(r.body as unknown as string) })
}

function mediaSource(ctx: Ctx, op: Op) {
  const b = op.body
  ctx.db.run('INSERT INTO media_sources (photo_id, device_id, source_ref, seen_hlc) VALUES (?, ?, ?, ?) ' +
    'ON CONFLICT(photo_id, device_id, source_ref) DO UPDATE SET seen_hlc = MIN(seen_hlc, excluded.seen_hlc)',
  [b.photo_id, b.device_id, b.source_ref, op.hlc])
}

function mediaSet(ctx: Ctx, op: Op) {
  const b = op.body
  if (!mediaExists(ctx, b.photo_id)) return defer(ctx, op, b.photo_id)
  const value = b.field === 'favorite' || b.field === 'hidden' ? Number(Boolean(b.value)) : b.value
  lwwRow(ctx, 'media', 'photo_id', b.photo_id, { [b.field]: value }, ver(EDIT, op.hlc))
  ctx.touched.add(b.photo_id)
}

function mediaDeleted(ctx: Ctx, op: Op) {
  const pid = op.body.photo_id
  if (!mediaExists(ctx, pid)) return defer(ctx, op, pid)
  lwwRow(ctx, 'media', 'photo_id', pid, { deleted: op.kind === 'media.delete' ? 1 : 0 }, ver(EDIT, op.hlc))
  ctx.touched.add(pid)
}

const setAdd = (table: string, col: string, lower: boolean) => (ctx: Ctx, op: Op) => {
  const b = op.body
  const value = lower ? norm(b[col]).toLowerCase() : norm(b[col])
  if (table === 'media_tags') ctx.db.run('INSERT OR IGNORE INTO media_tags (photo_id, tag, add_op, source) VALUES (?, ?, ?, ?)', [b.photo_id, value, op.op_id, 'user'])
  else ctx.db.run(`INSERT OR IGNORE INTO ${table} (photo_id, ${col}, add_op) VALUES (?, ?, ?)`, [b.photo_id, value, op.op_id])
  ctx.touched.add(b.photo_id)
}

function setRemove(ctx: Ctx, op: Op) {
  const b = op.body
  for (const a of b.observed_add_ops as string[]) {
    ctx.db.run('INSERT INTO tombstones (add_op, removed_by) VALUES (?, ?) ON CONFLICT(add_op) DO UPDATE SET removed_by = MIN(removed_by, excluded.removed_by)', [a, op.op_id])
  }
  ctx.touched.add(b.photo_id)
  if ('person_id' in b) ctx.people.add(b.person_id)
}

function mediaPersonAdd(ctx: Ctx, op: Op) {
  const b = op.body
  ctx.db.run('INSERT OR IGNORE INTO media_people (photo_id, person_id, source, add_op) VALUES (?, ?, ?, ?)', [b.photo_id, b.person_id, 'user', op.op_id])
  ctx.touched.add(b.photo_id)
}

function personName(ctx: Ctx, op: Op) {
  const b = op.body
  lwwRow(ctx, 'people', 'person_id', b.person_id, { name: norm(op.kind === 'person.create' ? b.name : b.value) }, ver(EDIT, op.hlc))
  ctx.people.add(b.person_id)
}

function personMerge(ctx: Ctx, op: Op) {
  const b = op.body
  if (b.from === b.into) return
  lwwRow(ctx, 'people', 'person_id', b.from, { merged_into: b.into }, ver(EDIT, op.hlc))
  ctx.people.add(b.from); ctx.people.add(b.into)
}

function device(ctx: Ctx, op: Op) {
  const { body: b, hlc } = op
  const values = op.kind === 'device.register' ? { name: b.name, platform: b.platform } : { [b.field]: b.value }
  lwwRow(ctx, 'devices', 'device_id', b.device_id, values, ver(EDIT, hlc))
  ctx.db.run('UPDATE devices SET first_seen_hlc = MIN(COALESCE(first_seen_hlc, ?), ?), last_seen_hlc = MAX(COALESCE(last_seen_hlc, ?), ?) WHERE device_id = ?',
    [hlc, hlc, hlc, hlc, b.device_id])
}

const HANDLERS: Record<string, (ctx: Ctx, op: Op) => void> = {
  'media.upsert': mediaUpsert, 'media.source': mediaSource, 'media.set': mediaSet,
  'media.delete': mediaDeleted, 'media.restore': mediaDeleted,
  'tag.add': setAdd('media_tags', 'tag', true), 'tag.remove': setRemove,
  'album.add': setAdd('media_albums', 'album', false), 'album.remove': setRemove,
  'media.person.add': mediaPersonAdd, 'media.person.remove': setRemove,
  'person.create': personName, 'person.set': personName, 'person.merge': personMerge,
  'device.register': device, 'device.set': device,
}

function dispatch(ctx: Ctx, op: Op) {
  const h = HANDLERS[op.kind]
  if (!h) throw new Error(`unknown op kind ${op.kind}`)
  h(ctx, op)
}

function refresh(ctx: Ctx) {
  if (ctx.people.size) {
    const ids = new Set(ctx.people)
    for (;;) {
      const q = [...ids]
      const more = ctx.db.all<{ person_id: string }>(`SELECT person_id FROM people WHERE merged_into IN (${q.map(() => '?').join(',')})`, q)
        .map((r) => r.person_id).filter((p) => !ids.has(p))
      if (!more.length) break
      more.forEach((p) => ids.add(p))
    }
    const q = [...ids]
    for (const r of ctx.db.all<{ photo_id: string }>(`SELECT DISTINCT photo_id FROM media_people WHERE person_id IN (${q.map(() => '?').join(',')})`, q)) {
      ctx.touched.add(r.photo_id)
    }
  }
  for (const pid of [...ctx.touched].sort()) refreshFts(ctx.db, pid)
}

function storeOp(db: Db, op: Op): boolean {
  if (db.get('SELECT 1 AS x FROM ops WHERE op_id = ?', [op.op_id])) return false
  db.run('INSERT INTO ops (op_id, hlc, device_id, kind, entity, body, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
    [op.op_id, op.hlc, op.device_id, op.kind, op.entity, canonical(op.body), new Date().toISOString().slice(0, 19) + '+00:00'])
  return true
}

/** Apply ops in any order and with duplicates; returns how many were new. */
export function applyOps(db: Db, ops: Op[], validate?: (op: Op) => void): number {
  const ordered = [...ops].sort((a, b) => (a.hlc < b.hlc ? -1 : a.hlc > b.hlc ? 1 : 0))
  if (validate) ordered.forEach(validate)
  return db.tx(() => {
    const ctx = new Ctx(db)
    let n = 0
    for (const op of ordered) {
      if (!storeOp(db, op)) continue
      dispatch(ctx, op)
      n++
    }
    refresh(ctx)
    return n
  })
}
