import { expect, test } from '@playwright/test'

test.describe.configure({ mode: 'serial' })

test('dry run, then archive from (fake) Apple Photos with live progress', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'archives into a shared server')
  await page.goto('/#/uploads')
  await page.getByLabel('What to archive').selectOption('all')
  await page.getByLabel(/Dry run/).check()
  await page.getByRole('button', { name: 'Count photos' }).click()
  await expect(page.getByRole('heading', { name: 'Finished' })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/8 of 8 done/)).toBeVisible()

  await page.getByLabel(/Dry run/).uncheck()
  await page.getByRole('button', { name: 'Start archive' }).click()
  await expect(page.getByRole('heading', { name: 'Finished' })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/8 of 8 done/)).toBeVisible()
  await expect(page.getByText(/Backed up databases/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('heading', { name: 'Recent runs' })).toBeVisible()
  const r = await (await page.request.get('/api/search?q=garden')).json()
  expect(r.items.length).toBe(8)
})

test('upload photos from this device', async ({ page }, info) => {
  test.skip(info.project.name !== 'pixel', 'one phone is enough')
  await page.goto('/#/uploads')
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64')
  await page.getByLabel('Choose photos and videos').setInputFiles([
    { name: 'phone-shot.png', mimeType: 'image/png', buffer: png },
  ])
  await expect(page.getByText('Uploaded')).toBeVisible({ timeout: 30_000 })
  const device = await page.evaluate(() => JSON.parse(localStorage.getItem('photoctl.device')!).device_id)
  await expect.poll(async () => (await (await page.request.get(`/api/search?device=${device}`)).json()).items.length,
                    { timeout: 30_000 }).toBe(1)
})
