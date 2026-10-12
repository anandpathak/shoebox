import { expect, test } from '@playwright/test'

test('uploads picked while the Mac is unreachable wait, then go when it is back', async ({ page, context }) => {
  await page.goto('/#/uploads')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  if (await dialog.isVisible()) await dialog.getByRole('button', { name: 'Save' }).click()
  await context.setOffline(true)
  await expect(page.locator('.status')).toContainText('Offline')
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAMAAAABCAYAAAAb4BS0AAAAEklEQVR42mP8z8DwnwEJMBJnAAA2iAMBmmYHXQAAAABJRU5ErkJggg==', 'base64')
  await page.getByLabel('Choose photos and videos').setInputFiles([{ name: 'offline-shot.png', mimeType: 'image/png', buffer: png }])
  await expect(page.getByText('Waiting for your Mac')).toBeVisible()

  await page.reload().catch(() => {})                     // still offline: the shell comes from the service worker
  await context.setOffline(false)
  await page.goto('/#/uploads')
  await expect(page.getByText('Uploaded', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect.poll(async () => (await (await page.request.get('/api/search?q=offline')).json()).items.length,
                    { timeout: 30_000 }).toBe(1)
})
