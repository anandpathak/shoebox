import { expect, test } from '@playwright/test'

test('a phone shares its on-device catalog; the Mac lists it with the right platform and counts', async ({ page }) => {
  await page.goto('/#/catalogs')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  if (await dialog.isVisible()) {
    await dialog.getByLabel('Device name').fill('Test Pixel')
    await dialog.getByRole('button', { name: 'Save' }).click()
  }
  // upload one photo from this phone so it has something of its own
  await page.goto('/#/uploads')
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAYAAAABCAYAAADeko4lAAAAEUlEQVR42mNkYPj/n4EBAwYAGfQCAU1nrcwAAAAASUVORK5CYII=', 'base64')
  await page.getByLabel('Choose photos and videos').setInputFiles([{ name: 'publish-me.png', mimeType: 'image/png', buffer: png }])
  await expect.poll(async () => (await (await page.request.get('/api/search?q=publish')).json()).items.length, { timeout: 30_000 }).toBe(1)

  await page.goto('/#/catalogs')
  await page.getByRole('button', { name: 'Use on this device' }).click()
  await expect(page.getByText('On this device', { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: "Share this device's catalog" }).click()
  await expect(page.getByText(/Shared: \d+ items, 1 from this device/)).toBeVisible({ timeout: 30_000 })
  const listing = await (await page.request.get('/api/catalogs')).json()
  const mine = listing.devices.find((d: { name: string }) => d.name === 'Test Pixel')
  expect(mine.platform).toBe('android')
  expect(mine.contributed_count).toBe(1)
})
