import { expect, test } from '@playwright/test'

test('sync screen: devices, sync now, converged, backups', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'writes to a shared server')
  await page.goto('/#/sync')
  const devices = page.locator('.card', { hasText: 'Devices' })
  await expect(devices).toContainText('Pixel 8')
  await expect(devices).toContainText('Android')
  await expect(devices).toContainText('iPhone / iPad')
  await expect(page.getByRole('heading', { name: /changes waiting to sync/ })).toBeVisible()
  await page.getByRole('button', { name: 'Sync now' }).click()
  await expect(page.getByRole('heading', { name: 'Up to date' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/Last sync just now/)).toBeVisible()
  await page.getByRole('button', { name: 'Back up databases now' }).click()
  await expect(page.getByText(/Last backup just now/)).toBeVisible({ timeout: 20_000 })
})
