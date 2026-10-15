import { expect, test } from '@playwright/test'

// Browsing without the Mac: the page reads the library file, thumbnails and copies straight from S3 (here
// the local S3 stand-in) with the read-only key, and never calls the Mac's API.
test('browse the library read-only from S3, without the Mac', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop' && info.project.name !== 'iphone', 'one desktop, one phone')
  await page.goto('/')
  const access = await (await page.request.get('/api/viewer/access')).json()
  expect(access.key_id).toBe('e2e-reader')
  await page.evaluate((a) => localStorage.setItem('shoebox.viewerAccess', JSON.stringify(a)), access)

  const macCalls: string[] = []
  page.on('request', (r) => { if (new URL(r.url()).pathname.startsWith('/api/')) macCalls.push(r.url()) })
  await page.goto('/?viewer=1#/library')
  await expect(page.getByText(/Read-only · library from/)).toBeVisible({ timeout: 30_000 })
  const tiles = page.locator('.tile')
  await expect(tiles.first()).toBeVisible()
  await expect.poll(() => tiles.first().locator('img').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true)
  expect(await tiles.first().locator('img').getAttribute('src')).toContain('X-Amz-Signature=')

  const nav = page.getByRole('navigation', { name: 'Sections' })
  await expect(nav.getByRole('link')).toHaveText(['Library', 'People'])

  await page.getByLabel('Search the library').fill('beach')
  await expect(page).toHaveURL(/q=beach/)
  const expected = (await (await page.request.get('/api/search?q=beach&limit=500')).json()).items.length
  await expect(tiles).toHaveCount(Math.min(expected, 120), { timeout: 15_000 })

  await tiles.first().click()
  await expect(page.getByRole('dialog', { name: 'Photo viewer' })).toBeVisible()
  await expect(page.getByLabel('Title')).toHaveCount(0)                            // nothing to edit
  await expect(page.getByRole('button', { name: /Request original/ })).toHaveCount(0)
  const dl = await page.getByRole('link', { name: /Download photo/ }).getAttribute('href')
  expect(dl).toContain('response-content-disposition')
  expect(macCalls).toEqual([])                                                       // the Mac wasn't needed
})

test('without a key, it explains how to set it up', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'one is enough')
  await page.goto('/?viewer=1#/library')
  await expect(page.getByRole('heading', { name: 'Set up browsing without your Mac' })).toBeVisible()
})
