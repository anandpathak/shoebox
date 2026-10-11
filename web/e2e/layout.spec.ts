import { expect, test } from '@playwright/test'

test('shell fits the screen and shows navigation and status', async ({ page }, info) => {
  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('Online via')
  const nav = page.getByRole('navigation', { name: 'Sections' })
  for (const label of ['Library', 'People', 'Sync']) await expect(nav.getByRole('link', { name: label })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  const box = await nav.boundingBox()
  if (info.project.name === 'desktop') expect(box!.x).toBe(0)                          // sidebar on the left
  else expect(box!.y + box!.height).toBeGreaterThan(page.viewportSize()!.height - 5)   // tab bar at the bottom
  await page.screenshot({ path: `test-results/shell-${info.project.name}.png` })
})
