import { expect, test } from '@playwright/test'

test('open a photo, step through, close', async ({ page }) => {
  await page.goto('/#/library?type=photo')              // a known photo list, whatever other tests added
  await expect(page).toHaveURL(/type=photo/)
  await expect(page.locator('.tile').first()).toBeVisible()
  const firstId = await page.locator('.tile').first().getAttribute('data-id')
  await page.locator('.tile').first().click()
  const viewer = page.getByRole('dialog', { name: 'Photo viewer' })
  await expect(viewer).toBeVisible()
  await expect(page).toHaveURL(new RegExp(`media/${firstId}`))
  await expect(viewer.locator('.viewer-media img')).toBeVisible()
  await expect(viewer.locator('.facts')).toContainText('Taken')
  await expect(viewer.locator('.facts')).toContainText('From')
  const wide = await viewer.evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(wide).toBeLessThanOrEqual(0)                       // nothing pushes the viewer wider than the screen

  await viewer.getByRole('button', { name: 'Next' }).click()
  await expect(page).not.toHaveURL(new RegExp(`media/${firstId}`))
  await page.keyboard.press('ArrowLeft')
  await expect(page).toHaveURL(new RegExp(`media/${firstId}`))
  await expect(viewer.locator('.facts')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(viewer).toBeHidden()
  await expect(page.locator('.tile').first()).toBeVisible()
})

test('swipe moves to the next item on touch screens', async ({ page }, info) => {
  test.skip(info.project.name === 'desktop', 'touch only')
  await page.goto('/')
  const firstId = await page.locator('.tile').first().getAttribute('data-id')
  await page.locator('.tile').first().click()
  const media = page.locator('.viewer-media')
  const box = (await media.boundingBox())!
  await media.dispatchEvent('pointerdown', { clientX: box.x + box.width * 0.8, clientY: box.y + box.height / 2 })
  await media.dispatchEvent('pointerup', { clientX: box.x + box.width * 0.2, clientY: box.y + box.height / 2 })
  await expect(page).not.toHaveURL(new RegExp(`media/${firstId}`))
})

test('videos play, downloads work, originals explain the wait', async ({ page }) => {
  await page.goto('/#/library?type=video')
  await page.locator('.tile').first().click()
  const viewer = page.getByRole('dialog', { name: 'Photo viewer' })
  await expect(viewer.locator('video')).toBeVisible()
  await expect(viewer.locator('.facts')).toContainText('Length')
  const href = await viewer.getByRole('link', { name: /Download video/ }).getAttribute('href')
  const dl = await page.request.get(href!)
  expect(dl.status()).toBe(200)
  expect(dl.headers()['content-disposition']).toContain('attachment')
  await expect(viewer.getByText(/about 12 hours/)).toBeVisible()
  await viewer.getByRole('button', { name: 'Request original' }).click()
  // the mock S3 restores instantly; real Deep Archive shows "Original requested" for ~12 h first
  await expect(viewer.getByRole('link', { name: 'Download original' }).or(viewer.getByText('Original requested'))).toBeVisible()
})
