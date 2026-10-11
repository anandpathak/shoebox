import { expect, test } from '@playwright/test'

async function apiCount(page: import('@playwright/test').Page, params: Record<string, string>) {
  const r = await page.request.get(`/api/search?${new URLSearchParams({ ...params, limit: '500' })}`)
  return (await r.json()).items.length as number
}

test('typed search matches the API (same query as photoctl search)', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Search the library').fill('beach')
  await expect(page).toHaveURL(/q=beach/)
  const expected = await apiCount(page, { q: 'beach' })
  expect(expected).toBeGreaterThan(0)
  await expect(page.locator('.tile')).toHaveCount(Math.min(expected, 120))
  await page.getByLabel('Search the library').fill('december 2024')
  await expect(page.locator('.understood')).toContainText('December 2024')
})

test('chips filter by type, favourites, device and person', async ({ page }) => {
  await page.goto('/')
  const filters = page.getByRole('toolbar', { name: 'Filters' })
  await filters.getByRole('button', { name: 'Videos' }).click()
  await expect(page).toHaveURL(/type=video/)
  await expect(page.locator('.tile')).toHaveCount(Math.min(await apiCount(page, { type: 'video' }), 120))
  expect(await page.locator('.tile:not(:has(.badge))').count()).toBe(0)
  await filters.getByRole('button', { name: 'Favourites' }).click()
  await expect(page.locator('.tile')).toHaveCount(await apiCount(page, { type: 'video', fav: 'true' }))
  expect(await page.locator('.tile:not(:has(.fav))').count()).toBe(0)
  await filters.getByRole('button', { name: 'Clear' }).click()
  await filters.getByRole('button', { name: 'Pixel 8' }).click()
  await expect(page.locator('.tile')).toHaveCount(Math.min(await apiCount(page, { device: '01E2EPIXEL0000000000000000' }), 120))
  await filters.getByRole('button', { name: 'Clear' }).click()
  await filters.getByRole('button', { name: 'Asha' }).click()
  await expect(page.locator('.tile')).toHaveCount(Math.min(await apiCount(page, { person: 'Asha' }), 120))
})

test('no matches shows a helpful empty state', async ({ page }) => {
  await page.goto('/#/library?q=zzzznotathing')
  await expect(page.getByText('No matches')).toBeVisible()
})
