import sqlite3InitModule from '@sqlite.org/sqlite-wasm'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, describe, expect, it } from 'vitest'
import { wrap, type Db } from './dbapi'
import { search } from './query'
import { MIGRATIONS } from './sql'

const parity = JSON.parse(readFileSync(resolve(__dirname, '../../../spec/search_parity.json'), 'utf8'))
let db: Db

beforeAll(async () => {
  const sqlite3 = await (sqlite3InitModule as unknown as (o: object) => Promise<any>)({ print: () => {}, printErr: () => {} })  // eslint-disable-line @typescript-eslint/no-explicit-any
  const raw = new sqlite3.oo1.DB(':memory:')
  for (const [, sql] of MIGRATIONS) raw.exec(sql)
  raw.exec(parity.catalog_sql)
  db = wrap(raw)
})

describe('on-device search returns what the server returns', () => {
  for (const c of parity.cases) {
    it(JSON.stringify(c.params), () => {
      const r = search(db, { ...c.params })
      expect(r.items.map((i) => i.photo_id)).toEqual(c.ids)
      expect(r.next !== null).toBe(c.has_next)
    })
  }
  it('has enough cases', () => expect(parity.cases.length).toBeGreaterThanOrEqual(20))
})
