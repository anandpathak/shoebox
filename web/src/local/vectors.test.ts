// spec/sync_vectors: the same scenarios the Python apply_ops passes, in the same delivery orders.
import sqlite3InitModule from '@sqlite.org/sqlite-wasm'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, describe, expect, it } from 'vitest'
import { applyOps, type Op } from './apply'
import { wrap, type Db } from './dbapi'
import { HLC } from './hlc'
import { makeOp, validate } from './ops'
import { MIGRATIONS } from './sql'
import { resolvePerson } from './store'

const dir = resolve(__dirname, '../../../spec/sync_vectors')
const vectors = readdirSync(dir).filter((f) => f.endsWith('.json')).sort().map((f) => JSON.parse(readFileSync(resolve(dir, f), 'utf8')))
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let sqlite3: any
beforeAll(async () => { sqlite3 = await (sqlite3InitModule as unknown as (o: object) => Promise<unknown>)({ print: () => {}, printErr: () => {} }) })

function fresh(): Db {
  const raw = new sqlite3.oo1.DB(':memory:')
  for (const [, sql] of MIGRATIONS) raw.exec(sql)
  return wrap(raw)
}

const LIVE = 'add_op NOT IN (SELECT add_op FROM tombstones)'

function orders(ops: Op[]): [string, Op[][]][] {
  return [['forward', [ops]], ['reverse', [[...ops].reverse()]], ['one_by_one_reversed', [...ops].reverse().map((o) => [o])],
    ['duplicated', [ops.filter((_, i) => i % 2 === 0), ops, [...ops].reverse()]]]
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function check(db: Db, expect_: any) {
  for (const [pid, cols] of Object.entries(expect_.media ?? {})) {
    const row = db.get<Record<string, unknown>>('SELECT * FROM media WHERE photo_id = ?', [pid])
    expect(row, pid).toBeTruthy()
    for (const [k, v] of Object.entries(cols as object)) expect(row![k], `${pid}.${k}`).toEqual(v)
  }
  for (const [key, table, col] of [['tags', 'media_tags', 'tag'], ['albums', 'media_albums', 'album']]) {
    for (const [pid, values] of Object.entries(expect_[key] ?? {})) {
      const got = [...new Set(db.all<Record<string, string>>(`SELECT ${col} AS v FROM ${table} WHERE photo_id = ? AND ${LIVE}`, [pid]).map((r) => r.v))].sort()
      expect(got, `${key} ${pid}`).toEqual([...(values as string[])].sort())
    }
  }
  for (const [person, cols] of Object.entries(expect_.people ?? {}) as [string, { name?: string; resolves_to?: string }][]) {
    if (cols.name !== undefined) expect(db.get<{ name: string }>('SELECT name FROM people WHERE person_id = ?', [person])!.name).toBe(cols.name)
    if (cols.resolves_to !== undefined) expect(resolvePerson(db, person)).toBe(cols.resolves_to)
  }
  for (const [pid, names] of Object.entries(expect_.photo_people ?? {})) {
    const ids = new Set(db.all<{ person_id: string }>(`SELECT person_id FROM media_people WHERE photo_id = ? AND ${LIVE}`, [pid]).map((r) => resolvePerson(db, r.person_id)))
    const got = [...ids].map((i) => db.get<{ name: string }>('SELECT name FROM people WHERE person_id = ?', [i])!.name).sort()
    expect(got).toEqual([...(names as string[])].sort())
  }
  for (const [table, n] of Object.entries(expect_.counts ?? {})) expect(db.get<{ n: number }>(`SELECT COUNT(*) AS n FROM ${table}`)!.n, table).toBe(n)
  for (const [dev, cols] of Object.entries(expect_.devices ?? {})) {
    const row = db.get<Record<string, unknown>>('SELECT * FROM devices WHERE device_id = ?', [dev])!
    for (const [k, v] of Object.entries(cols as object)) expect(row[k]).toEqual(v)
  }
  for (const [q, pids] of Object.entries(expect_.fts ?? {})) {
    const got = db.all<{ photo_id: string }>('SELECT photo_id FROM media_fts WHERE media_fts MATCH ?', [q]).map((r) => r.photo_id).sort()
    expect(got, `fts ${q}`).toEqual([...(pids as string[])].sort())
  }
  for (const [pid, sources] of Object.entries(expect_.sources ?? {}) as [string, Record<string, string>][]) {
    const got: Record<string, string> = {}
    for (const r of db.all<{ k: string; s: string }>(`SELECT tag AS k, source AS s FROM media_tags WHERE photo_id = ? AND ${LIVE}`, [pid])) got[r.k] = r.s
    for (const r of db.all<{ k: string; s: string }>(`SELECT p.name AS k, m.source AS s FROM media_people m JOIN people p USING (person_id) WHERE m.photo_id = ? AND m.${LIVE}`, [pid])) got[r.k.toLowerCase()] = r.s
    expect(Object.fromEntries(Object.keys(sources).map((k) => [k, got[k]])), `sources ${pid}`).toEqual(sources)
  }
  for (const [addOp, remover] of Object.entries(expect_.tombstones ?? {})) {
    expect(db.get<{ removed_by: string }>('SELECT removed_by FROM tombstones WHERE add_op = ?', [addOp])!.removed_by).toBe(remover)
  }
}

describe('sync conformance vectors (shared with Python)', () => {
  for (const v of vectors) {
    it(v.name, () => {
      for (const [name, batches] of orders(v.ops)) {
        const db = fresh()
        for (const b of batches) applyOps(db, b, validate)
        try { check(db, v.expect) } catch (e) { throw new Error(`${v.name} [${name}]: ${(e as Error).message}`) }
      }
    })
  }
  it('has every vector', () => expect(vectors.length).toBeGreaterThanOrEqual(20))
})

describe('HLC and ops', () => {
  it('is monotonic when the wall clock goes backwards, and observes remote values', () => {
    const t = [1000, 1000, 900, 1200]
    const c = new HLC('DEV', null, () => t.shift()!)
    const vals = [c.now(), c.now(), c.now(), c.now()]
    expect([...vals].sort()).toEqual(vals)
    expect(vals[2]).toBe('0000000001000-0002-DEV')
    const d = new HLC('A', null, () => 1000)
    d.observe('0000000005000-0003-B')
    expect(d.now()).toBe('0000000005000-0004-A')
  })
  it('makes valid ops and rejects bad ones', () => {
    const c = new HLC('PHONE', null, () => 1_700_000_000_000)
    const op = makeOp(c, 'media.set', { photo_id: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', field: 'title', value: 'x' })
    expect(op.op_id).toBe(op.hlc)
    expect(() => makeOp(c, 'media.set', { photo_id: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', field: 'raw_key', value: 'x' })).toThrow(/invalid/)
  })
})
