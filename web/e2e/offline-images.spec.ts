import { expect, test } from '@playwright/test'

test('thumbnails for a year and saved photos show offline', async ({ page, context }, info) => {
  test.skip(info.project.name === 'iphone' || info.project.name.startsWith('tailnet'), 'Chrome projects with the service worker')
  await page.goto('/')
  await expect.poll(() => page.evaluate(async () => !!(await navigator.serviceWorker.ready).active)).toBe(true)
  await page.reload()
  const year = (await (await page.request.get('/api/search?limit=1')).json()).items[0].taken_at.slice(0, 4)
  await page.goto('/#/catalogs')
  await page.getByRole('button', { name: 'Use on this device' }).click()
  await expect(page.getByText('On this device', { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.getByLabel('Year to download').selectOption(year)
  await page.getByRole('button', { name: 'Download thumbnails' }).click()
  await expect(page.getByText(new RegExp(`Thumbnails for ${year} are on this device`))).toBeVisible({ timeout: 60_000 })

  const photo = (await (await page.request.get(`/api/search?type=photo&from=${year}-01-01&limit=1`)).json()).items[0].photo_id
  await page.goto(`/#/media/${photo}`)
  await page.getByRole('button', { name: 'Details' }).click()
  await page.getByRole('button', { name: 'Save offline' }).click()
  await expect(page.getByRole('button', { name: 'Saved offline' })).toBeVisible()

  await context.setOffline(true)
  await page.goto('/#/library')
  await expect(page.getByRole('status').first()).toContainText('on-device catalog', { timeout: 40_000 })
  const img = page.locator(`.tile[data-id="${photo}"] img`)
  await expect(img).toBeVisible({ timeout: 15_000 })
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth)).toBeGreaterThan(0)
  await page.goto(`/#/media/${photo}`)
  const full = page.locator('.viewer-media img')
  await expect.poll(() => full.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth), { timeout: 15_000 }).toBeGreaterThan(0)
  await context.setOffline(false)
})
