import { expect, test } from '@playwright/test'

test('picking photos that are already archived uploads only the new ones', async ({ page }) => {
  await page.goto('/#/uploads')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  if (await dialog.isVisible()) await dialog.getByRole('button', { name: 'Save' }).click()
  const a = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAQAAAABCAYAAAD5PA/NAAAAEklEQVR42mNkYPj/n4GBgYGBAQAWBgICOXcj7wAAAABJRU5ErkJggg==', 'base64')
  const b = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAUAAAABCAYAAAAW/mK4AAAAEUlEQVR42mNkYPj/n4EBDAAZ9AIBhTp2xwAAAABJRU5ErkJggg==', 'base64')
  const input = page.getByLabel('Choose photos and videos')
  await input.setInputFiles([{ name: 'dup-a.png', mimeType: 'image/png', buffer: a }])
  await expect.poll(async () => (await (await page.request.get('/api/search?q=dup')).json()).items.length, { timeout: 30_000 }).toBe(1)
  await page.reload()
  await page.getByLabel('Choose photos and videos').setInputFiles([
    { name: 'dup-a-again.png', mimeType: 'image/png', buffer: a },
    { name: 'dup-b.png', mimeType: 'image/png', buffer: b },
  ])
  await expect(page.getByRole('status').filter({ hasText: 'already backed up' })).toContainText('1 already backed up · 1 to upload')
  await expect.poll(async () => (await (await page.request.get('/api/search?q=dup')).json()).items.length, { timeout: 30_000 }).toBe(2)
})
