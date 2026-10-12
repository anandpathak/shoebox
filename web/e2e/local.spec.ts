import { expect, test } from './fixtures'

test('a downloaded catalog opens on the device and full-text search runs there', async ({ devicePage: page, browserName }) => {
  await page.goto('/')
  const result = await page.evaluate(async () => {
    const { local, gunzip } = (globalThis as unknown as { photoctlLocal: { local: { call: (op: string, ...a: unknown[]) => Promise<unknown> }, gunzip: (b: ArrayBuffer) => Promise<Uint8Array> } }).photoctlLocal
    const status = await local.call('status') as { persistent: boolean }
    const res = await fetch('/api/catalogs/library')
    const bytes = await gunzip(await res.arrayBuffer())
    const imported = await local.call('importCatalog', 'library', bytes) as { items: number }
    const hits = await local.call('query', 'library', "SELECT COUNT(*) AS n FROM media_fts WHERE media_fts MATCH 'gokarna'") as { n: number }[]
    return { persistent: status.persistent, items: imported.items, hits: hits[0].n }
  })
  expect(result.persistent).toBe(true)          // OPFS storage is available
  expect(result.items).toBeGreaterThan(0)
  expect(result.hits).toBeGreaterThan(0)

  // survives a reload: the file stays in the device's private storage
  if (browserName !== 'webkit') await page.reload()   // WebKit: see fixtures.ts
  else await page.goto('/#/library')
  const again = await page.evaluate(async () => {
    const { local } = (globalThis as unknown as { photoctlLocal: { local: { call: (op: string, ...a: unknown[]) => Promise<unknown> } } }).photoctlLocal
    return (await local.call('status') as { databases: string[] }).databases
  })
  expect(again).toContain('library')
})
