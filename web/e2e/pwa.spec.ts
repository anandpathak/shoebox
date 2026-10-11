import { expect, test } from '@playwright/test'

test('installable: manifest, icons and service worker', async ({ page }, info) => {
  test.skip(info.project.name === 'iphone', 'service workers are checked in Chrome projects')
  await page.goto('/')
  const href = await page.locator('link[rel=manifest]').getAttribute('href')
  const res = await page.request.get(href!)
  expect(res.headers()['content-type']).toContain('application/manifest+json')
  const manifest = await res.json()
  expect(manifest.display).toBe('standalone')
  for (const icon of manifest.icons) expect((await page.request.get(icon.src)).status()).toBe(200)
  expect(manifest.icons.some((i: { purpose: string }) => i.purpose === 'maskable')).toBe(true)
  await expect.poll(() => page.evaluate(async () => !!(await navigator.serviceWorker.ready).active)).toBe(true)
})

test('the app shell still opens offline', async ({ page, context }, info) => {
  test.skip(info.project.name === 'iphone', 'service workers are checked in Chrome projects')
  await page.goto('/')
  await expect.poll(() => page.evaluate(async () => !!(await navigator.serviceWorker.ready).active)).toBe(true)
  await page.reload()                                   // let the worker control the page
  await expect(page.locator('.tile').first()).toBeVisible()
  await context.setOffline(true)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Library' })).toBeVisible()
  await expect(page.locator('.status')).toContainText(/Offline/)
  await context.setOffline(false)
})

test('iPhone users see how to add to the home screen', async ({ page }, info) => {
  test.skip(info.project.name !== 'iphone', 'iOS hint')
  await page.goto('/')
  await expect(page.getByText('Add to Home Screen')).toBeVisible()
  await page.getByRole('button', { name: 'Not now' }).click()
  await page.reload()
  await expect(page.getByText('Add to Home Screen')).toBeHidden()
})
