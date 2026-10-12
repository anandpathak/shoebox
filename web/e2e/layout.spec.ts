import { expect, test } from '@playwright/test'

test('shell fits the screen and shows navigation and status', async ({ page }, info) => {
  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('Online via')
  const nav = page.getByRole('navigation', { name: 'Sections' })
  for (const label of ['Library', 'People', 'Sync']) await expect(nav.getByRole('link', { name: label })).toBeVisible()
  await expect(page.locator('.tile').first()).toBeVisible()
  await page.locator('.sentinel').scrollIntoViewIfNeeded()     // more months, some of them skipped by content-visibility
  await page.waitForTimeout(500)
  // On phones an over-wide element makes Chrome widen the whole layout viewport, so compare with the real screen.
  const widths = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, inner: innerWidth }))
  const screenWidth = page.viewportSize()!.width
  expect(widths.inner).toBe(screenWidth)
  expect(widths.doc).toBeLessThanOrEqual(screenWidth)
  const box = await nav.boundingBox()
  if (info.project.name === 'desktop') expect(box!.x).toBe(0)                          // sidebar on the left
  else expect(box!.y + box!.height).toBeGreaterThan(page.viewportSize()!.height - 5)   // tab bar at the bottom
  await page.screenshot({ path: `test-results/shell-${info.project.name}.png` })
})
