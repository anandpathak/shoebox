import { expect, test } from './fixtures'

const QUERIES = ['beach', 'gokarna', 'december 2024', 'videos', 'asha', 'lake boat', 'night lights', '2025']

test('offline, search runs on the device and matches what the server returned', async ({ devicePage: page, browserName }) => {
  const online: Record<string, string[]> = {}
  await page.goto('/')
  for (const q of QUERIES) {
    online[q] = (await (await page.request.get(`/api/search?q=${encodeURIComponent(q)}&limit=60`)).json()).items.map((i: { photo_id: string }) => i.photo_id)
  }
  await page.goto('/#/catalogs')
  await page.getByRole('button', { name: 'Use on this device' }).click()
  await expect(page.getByText('On this device', { exact: true })).toBeVisible({ timeout: 30_000 })

  await page.context().setOffline(true)
  await page.goto('/#/library').catch(() => {})
  if (browserName !== 'webkit') await page.reload().catch(() => {})   // WebKit: see fixtures.ts
  await expect(page.getByRole('status').first()).toContainText('on-device catalog', { timeout: 40_000 })
  // after a reload the new page may wait a moment for the old one to hand over the storage
  await expect(page.locator('.tile').first()).toBeVisible({ timeout: 15_000 })

  for (const q of QUERIES) {
    const ids = await page.evaluate(async (query) => {
      const { local } = (globalThis as unknown as { photoctlLocal: { local: { call: (o: string, ...a: unknown[]) => Promise<{ items: { photo_id: string }[] }> } } }).photoctlLocal
      return (await local.call('search', 'library', { q: query, limit: 60 })).items.map((i) => i.photo_id)
    }, q)
    expect(ids, q).toEqual(online[q])
  }
  // and through the UI
  await page.getByLabel('Search the library').fill('beach')
  await expect(page.locator('.tile')).toHaveCount(Math.min(online.beach.length, 120))
  await page.context().setOffline(false)
})
