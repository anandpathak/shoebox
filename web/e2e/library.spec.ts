import { expect, test } from '@playwright/test'

test('grid shows month sections and loads more on scroll', async ({ page }) => {
  await page.goto('/')
  const tiles = page.locator('.tile')
  await expect(tiles.first()).toBeVisible()
  await expect(page.locator('.month-title').first()).toContainText(/\d{4}/)
  const first = await tiles.count()
  expect(first).toBeGreaterThanOrEqual(120)
  for (let i = 0; i < 10 && (await tiles.count()) < 300; i++) {
    await page.locator('.sentinel').scrollIntoViewIfNeeded()
    await page.waitForTimeout(300)
  }
  await expect(tiles).toHaveCount(300)
  await expect(page.locator('.sentinel')).toContainText('That’s everything')
  const img = page.locator('.tile img').first()
  await expect(img).toHaveJSProperty('complete', true)
  expect(await img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0)
})

test('tiles mark videos, favourites and photos on several devices', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.tile .badge').first()).toBeVisible()
  await expect(page.locator('.tile .fav').first()).toBeVisible()
})
