// A tiny synchronous database interface over sqlite-wasm's oo1.DB, used by the on-device catalog
// (inside a Web Worker) and by unit tests in Node. Named parameters are written without the colon.

export type Params = Record<string, unknown> | unknown[] | undefined
export type Row = Record<string, unknown>

export interface Db {
  all<T = Row>(sql: string, params?: Params): T[]
  get<T = Row>(sql: string, params?: Params): T | undefined
  run(sql: string, params?: Params): void
  exec(sql: string): void
  tx<T>(fn: () => T): T
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type OO1 = any

function bind(params: Params) {
  if (!params || Array.isArray(params)) return params
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(params)) out[k.startsWith(':') ? k : `:${k}`] = v === undefined ? null : typeof v === 'boolean' ? Number(v) : v
  return out
}

export function wrap(db: OO1): Db {
  const all = <T>(sql: string, params?: Params): T[] => {
    const rows: T[] = []
    db.exec({ sql, bind: bind(params), rowMode: 'object', callback: (r: T) => { rows.push(r) } })
    return rows
  }
  let depth = 0
  return {
    all,
    get: <T>(sql: string, params?: Params) => all<T>(sql, params)[0],
    run: (sql, params) => { db.exec({ sql, bind: bind(params) }) },
    exec: (sql) => { db.exec(sql) },
    tx: (fn) => {
      if (depth > 0) return fn()
      depth++
      db.exec('BEGIN')
      try { const r = fn(); db.exec('COMMIT'); return r } catch (e) { db.exec('ROLLBACK'); throw e } finally { depth-- }
    },
  }
}
