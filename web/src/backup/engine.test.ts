import { describe, expect, it } from 'vitest'
import { runBackup, type Context } from './engine'
import { emptyLedger, retryFailed } from './ledger'
import { DEFAULT_SETTINGS, type Asset, type Ledger, type Phone, type UploadRequest, type UploadState } from './types'

const SHA = (s: string) => s.padEnd(64, '0')

/** A camera roll and the OS upload queue, in memory. */
class FakePhone implements Phone {
  assets = new Map<string, Asset & { sha: string }>()
  log: string[] = []                       // change log: asset ids in order, '-id' for deletions
  queue = new Map<string, UploadState & { req: UploadRequest }>()
  conds = { wifi: true, charging: true, online: true }
  accessValue: 'full' | 'limited' | 'denied' = 'full'
  hashFails = new Set<string>()
  hashed: string[] = []
  add(id: string, sha: string, extra: Partial<Asset> = {}) {
    this.assets.set(id, { id, sha: SHA(sha), modified: 1, created: Date.UTC(2026, 0, 1), kind: 'photo', filename: `${id}.heic`,
                          bytes: 100, ...extra })
    this.log.push(id)
  }
  edit(id: string, sha?: string) {
    const a = this.assets.get(id)!
    this.assets.set(id, { ...a, modified: a.modified + 1, sha: sha ? SHA(sha) : a.sha })
    this.log.push(id)
  }
  remove(id: string) { this.assets.delete(id); this.log.push(`-${id}`) }
  async access() { return this.accessValue }
  async requestAccess() { return this.accessValue }
  async changes(token: string | null) {
    const from = token ? Number(token) : 0
    const ids = this.log.slice(from)
    const deleted = ids.filter((i) => i.startsWith('-')).map((i) => i.slice(1))
    const assets = [...new Set(ids.filter((i) => !i.startsWith('-')))].map((i) => this.assets.get(i)).filter((a) => !!a)
    return { assets, deleted, token: String(this.log.length), full: token == null }
  }
  async hash(id: string) {
    if (this.hashFails.has(id)) throw new Error('disk full')
    this.hashed.push(id)
    const a = this.assets.get(id)!
    return { sha256: a.sha, bytes: a.bytes ?? 0 }
  }
  async upload(req: UploadRequest) { this.queue.set(req.assetId, { assetId: req.assetId, state: 'queued', req }) }
  async uploads() { return [...this.queue.values()] }
  async forget(ids: string[]) { ids.forEach((i) => this.queue.delete(i)) }
  async conditions() { return this.conds }
  /** The OS finishes every queued upload against the fake Mac. */
  deliver(mac: FakeMac) {
    for (const u of this.queue.values()) {
      if (u.state !== 'queued') continue
      const a = this.assets.get(u.assetId)
      const r = mac.receive(u.req, a?.sha)
      Object.assign(u, { state: r.status ? 'done' : 'failed', status: r.status, body: r.body, error: r.error })
    }
  }
}

class FakeMac {
  archive = new Set<string>()
  received: string[] = []
  down = false
  failNext = 0
  receive(req: UploadRequest, actualSha?: string): { status?: number; body?: string; error?: string } {
    if (this.failNext > 0) { this.failNext--; return { status: 500, body: '{}' } }
    if (actualSha !== req.headers['X-Sha256']) return { status: 422, body: '{}' }
    if (this.archive.has(req.sha256)) return { status: 200, body: '{"status":"already_archived"}' }
    this.archive.add(req.sha256)
    this.received.push(req.assetId)
    return { status: 200, body: '{"status":"received"}' }
  }
  fetch: typeof fetch = async (_url, init) => {
    if (this.down) throw new TypeError('Load failed')
    const { hashes } = JSON.parse(String(init!.body)) as { hashes: string[] }
    return new Response(JSON.stringify({ missing: hashes.filter((h) => !this.archive.has(h)),
                                         archived: hashes.filter((h) => this.archive.has(h)), in_progress: [] }))
  }
}

function setup(settings = {}) {
  const phone = new FakePhone(), mac = new FakeMac()
  let ledger: Ledger = emptyLedger()
  let t = 1_000_000
  const ctx: Context = {
    phone, fetch: mac.fetch, origin: 'https://mac.example.ts.net', deviceId: 'DEV1', platform: 'ios',
    settings: { ...DEFAULT_SETTINGS, enabled: true, ...settings },
    load: async () => structuredClone(ledger), save: async (l) => { ledger = structuredClone(l) }, now: () => t,
  }
  const run = () => runBackup(ctx)
  return { phone, mac, ctx, run, ledger: () => ledger, tick: (ms: number) => { t += ms } }
}

describe('automatic backup', () => {
  it('uploads new photos once and reports progress', async () => {
    const { phone, mac, run } = setup()
    phone.add('a', 'aa'); phone.add('b', 'bb')
    let s = await run()
    expect(s).toMatchObject({ total: 2, backedUp: 0, waiting: 2, waitingFor: 'mac' })
    expect([...phone.queue.values()][0].req.headers).toMatchObject({ 'X-Device-Id': 'DEV1', 'X-Asset-Id': 'a', 'X-Sha256': SHA('aa') })
    phone.deliver(mac)
    s = await run()
    expect(s).toMatchObject({ total: 2, backedUp: 2, waiting: 0, waitingFor: null })
    expect(mac.received).toEqual(['a', 'b'])
    expect(phone.queue.size).toBe(0)
    await run()
    expect(phone.hashed).toEqual(['a', 'b'])             // nothing hashed or sent again
  })

  it("skips photos the archive already has (e.g. from the Mac's Apple Photos)", async () => {
    const { phone, mac, run, ledger } = setup()
    mac.archive.add(SHA('aa'))
    phone.add('a', 'aa'); phone.add('b', 'bb')
    await run()
    expect(phone.queue.size).toBe(1)
    expect(ledger().entries.a).toMatchObject({ state: 'done', how: 'already' })
  })

  it('sends duplicates on the phone once', async () => {
    const { phone, mac, run } = setup()
    phone.add('a', 'same'); phone.add('b', 'same')
    await run(); phone.deliver(mac)
    const s = await run()
    expect(mac.received).toEqual(['a'])
    expect(s.backedUp).toBe(2)
  })

  it('edits: re-hashed, and only sent when the bytes changed', async () => {
    const { phone, mac, run, ledger } = setup()
    phone.add('a', 'v1')
    await run(); phone.deliver(mac); await run()
    phone.edit('a')                                       // e.g. favourited: same bytes
    await run()
    expect(ledger().entries.a.state).toBe('done')
    expect(phone.queue.size).toBe(0)
    phone.edit('a', 'v2')                                 // cropped: new bytes → a new item
    await run(); phone.deliver(mac); await run()
    expect(mac.received).toEqual(['a', 'a'])
    expect(mac.archive.has(SHA('v2'))).toBe(true)
  })

  it('waits for Wi-Fi, and keeps videos for when the phone is charging', async () => {
    const { phone, run } = setup()
    phone.add('p', 'pp'); phone.add('v', 'vv', { kind: 'video' })
    phone.conds = { wifi: false, charging: false, online: true }
    let s = await run()
    expect(s).toMatchObject({ waiting: 2, waitingFor: 'wifi' })
    expect(phone.queue.size).toBe(0)
    phone.conds = { wifi: true, charging: false, online: true }
    s = await run()
    expect([...phone.queue.keys()]).toEqual(['p'])
    expect(s.waitingFor).toBe('charging')
    phone.conds = { wifi: true, charging: true, online: true }
    await run()
    expect([...phone.queue.keys()]).toEqual(['p', 'v'])
  })

  it('settings: screenshots off by default, videos off, start date, albums', async () => {
    const { phone, run, ctx, ledger } = setup()
    phone.add('s', 's1', { screenshot: true })
    phone.add('old', 'o1', { created: Date.UTC(2020, 0, 1), albums: ['Trips'] })
    phone.add('v', 'v1', { kind: 'video' })
    phone.conds = { wifi: false, charging: true, online: true }   // nothing sent yet, so settings decide
    let s = await run()
    expect(s).toMatchObject({ total: 2, skipped: 1 })
    expect(ledger().entries.s.reason).toBe('Screenshots are off')
    ctx.settings = { ...ctx.settings, videos: false, from: Date.UTC(2025, 0, 1) }
    s = await run()
    expect(s).toMatchObject({ total: 0, skipped: 3 })
    ctx.settings = { ...ctx.settings, videos: true, from: null, albums: ['Trips'] }
    s = await run()
    expect(s).toMatchObject({ total: 1, skipped: 2 })     // only the item in "Trips"
  })

  it("when the Mac can't be reached nothing is lost, and it carries on later", async () => {
    const { phone, mac, run } = setup()
    phone.add('a', 'aa')
    mac.down = true
    let s = await run()
    expect(s).toMatchObject({ waiting: 1, waitingFor: 'mac' })
    expect(s.lastError).toMatch(/Can't reach your Mac/)
    mac.down = false
    await run(); phone.deliver(mac)
    s = await run()
    expect(s).toMatchObject({ backedUp: 1, lastError: undefined })
  })

  it('failed uploads are retried with back-off, then marked failed until retried', async () => {
    const { phone, mac, run, tick, ctx, ledger } = setup()
    phone.add('a', 'aa')
    mac.failNext = 100
    for (let i = 0; i < 8; i++) {
      await run(); phone.deliver(mac); await run()
      tick(7 * 3600_000)
    }
    let s = await run()
    expect(s.failed).toBe(1)
    expect(ledger().entries.a.reason).toMatch(/500/)
    mac.failNext = 0
    await ctx.save(retryFailed(ledger()))
    await run(); phone.deliver(mac)
    s = await run()
    expect(s).toMatchObject({ backedUp: 1, failed: 0 })
  })

  it('a file that changed in transit is read again', async () => {
    const { phone, mac, run, ledger } = setup()
    phone.add('a', 'v1')
    await run()
    phone.assets.get('a')!.sha = SHA('v2')               // bytes changed after hashing
    phone.deliver(mac)
    await run()
    expect(ledger().entries.a.state).toBe('pending')
    expect(ledger().entries.a.sha256).toBeUndefined()
  })

  it("can't read an original: retried later, other items carry on", async () => {
    const { phone, run, ledger } = setup()
    phone.add('a', 'aa'); phone.add('b', 'bb')
    phone.hashFails.add('a')
    await run()
    expect(ledger().entries.a).toMatchObject({ state: 'pending', attempts: 1, reason: expect.stringMatching(/disk full/) })
    expect(ledger().entries.b.state).toBe('uploading')
  })

  it('deleted items leave the ledger; uploads the OS lost are checked again', async () => {
    const { phone, mac, run, ledger } = setup()
    phone.add('a', 'aa'); phone.add('b', 'bb')
    await run()
    phone.remove('a')
    phone.queue.clear()                                   // app reinstalled: the OS queue is gone
    await run()
    expect(Object.keys(ledger().entries)).toEqual(['b'])
    expect(ledger().entries.b.state).toBe('uploading')    // checked again and re-queued
    phone.deliver(mac)
    expect((await run()).backedUp).toBe(1)
  })

  it('limited photo access is reported; no access does nothing', async () => {
    const { phone, run } = setup()
    phone.add('a', 'aa')
    phone.accessValue = 'limited'
    expect((await run()).access).toBe('limited')
    phone.accessValue = 'denied'
    const s = await run()
    expect(s.lastError).toBe('Photo access is off')
  })

  it('off means off', async () => {
    const { phone, run } = setup({ enabled: false })
    phone.add('a', 'aa')
    const s = await run()
    expect(s.enabled).toBe(false)
    expect(phone.hashed).toEqual([])
  })
})
