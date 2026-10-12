import { expect, test } from './fixtures'

test('use on this device, keep it across reloads, then remove it', async ({ devicePage: page, browserName }) => {
  await page.goto('/#/catalogs')
  await page.getByRole('button', { name: 'Use on this device' }).click()
  await expect(page.getByText('On this device', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/\d+ items · downloaded just now/)).toBeVisible()
  if (browserName !== 'webkit') await page.reload()   // WebKit: see fixtures.ts
  else { await page.goto('/#/library'); await page.goto('/#/catalogs') }
  await expect(page.getByText('On this device', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Remove downloaded data' }).click()
  await expect(page.getByRole('button', { name: 'Use on this device' })).toBeVisible()
})
