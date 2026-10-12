import { expect, localCall, test } from './fixtures'

test('download the iPhone and Android catalogs and search them together offline', async ({ devicePage: page }) => {
  const ids = async (device: string) =>
    (await (await page.request.get(`/api/search?q=beach&device=${device}&limit=500`)).json()).items as { photo_id: string; taken_at: string }[]
  await page.goto('/#/catalogs')
  const iphone = page.locator('.catalog-row', { hasText: "My iPhone" })
  const pixel = page.locator('.catalog-row', { hasText: 'Pixel 8' })
  await expect(iphone).toContainText('iPhone')
  await expect(pixel).toContainText('Android')
  const expected = [...await ids('01E2EIPHONE000000000000000'), ...await ids('01E2EPIXEL0000000000000000')]
    .sort((a, b) => (a.taken_at + a.photo_id < b.taken_at + b.photo_id ? 1 : -1)).map((i) => i.photo_id)
  expect(expected.length).toBeGreaterThan(0)

  await iphone.getByRole('button', { name: 'Download' }).click()
  await expect(iphone).toContainText('on this device', { timeout: 30_000 })
  await pixel.getByRole('button', { name: 'Download' }).click()
  await expect(pixel).toContainText('on this device', { timeout: 30_000 })

  await page.context().setOffline(true)
  await page.goto('/#/library')
  await expect(page.getByRole('status').first()).toContainText('searching downloaded catalogs', { timeout: 40_000 })
  const got = await localCall<{ items: { photo_id: string }[] }>(page, 'searchMany',
    ['dev-01E2EIPHONE000000000000000', 'dev-01E2EPIXEL0000000000000000'], { q: 'beach', limit: 500 })
  expect(got.items.map((i) => i.photo_id)).toEqual(expected)
  await page.getByLabel('Search the library').fill('beach')
  await expect(page.locator('.tile')).toHaveCount(Math.min(expected.length, 120), { timeout: 15_000 })

  // untick the Pixel catalog: only iPhone photos remain
  await page.context().setOffline(false)
  await page.goto('/#/catalogs')
  await page.getByRole('checkbox', { name: 'Search Pixel 8' }).uncheck()
  const onlyIphone = await localCall<{ items: unknown[] }>(page, 'searchMany', ['dev-01E2EIPHONE000000000000000'], { q: 'beach', limit: 500 })
  expect(onlyIphone.items.length).toBe((await ids('01E2EIPHONE000000000000000')).length)
  await page.getByRole('checkbox', { name: 'Search Pixel 8' }).check()
  await page.locator('.catalog-row', { hasText: 'Pixel 8' }).getByRole('button', { name: 'Remove' }).click()
  await expect(page.locator('.catalog-row', { hasText: 'Pixel 8' })).not.toContainText('on this device')
})

test('a catalog shared again after it was downloaded is marked as stale, and Update refreshes it', async ({ devicePage: page }) => {
  await page.goto('/#/catalogs')
  const iphone = page.locator('.catalog-row', { hasText: "My iPhone" })
  await iphone.getByRole('button', { name: 'Download' }).click()
  await expect(iphone).toContainText('on this device', { timeout: 30_000 })
  await expect(iphone.getByText('newer version available')).toHaveCount(0)
  // pretend this copy was downloaded from an older share
  await page.evaluate(() => {
    const st = JSON.parse(localStorage.getItem('photoctl.local')!)
    st.devices['01E2EIPHONE000000000000000'].createdAt = '2000-01-01T00:00:00+00:00'
    localStorage.setItem('photoctl.local', JSON.stringify(st))
  })
  await page.reload()
  await expect(iphone.getByText('newer version available')).toBeVisible()
  await iphone.getByRole('button', { name: 'Update' }).click()
  await expect(iphone.getByText('newer version available')).toHaveCount(0, { timeout: 30_000 })
})
