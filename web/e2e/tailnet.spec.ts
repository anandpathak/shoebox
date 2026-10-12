import { expect, test } from '@playwright/test'

test('a phone on the tailnet names itself, registers, and its uploads are credited to it', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('tailnet')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Device name').fill("My Pixel")
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  const devices = await (await page.request.get('/api/devices')).json()
  const me = devices.find((d: { name: string }) => d.name === "My Pixel")
  expect(me.platform).toBe('android')
  await page.reload()
  await expect(page.getByRole('dialog', { name: 'Name this device' })).toBeHidden()

  await page.goto('/#/uploads')
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR42mNkYPhfz8DAwMAAAA4mAgHtbmYOAAAAAElFTkSuQmCC', 'base64')
  await page.getByLabel('Choose photos and videos').setInputFiles([{ name: 'from-pixel.png', mimeType: 'image/png', buffer: png }])
  await expect(page.getByText('Uploaded', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect.poll(async () => (await (await page.request.get(`/api/search?device=${me.device_id}`)).json()).items.length,
                    { timeout: 30_000 }).toBe(1)
  // the viewer says the phone's upload has no location
  const id = (await (await page.request.get(`/api/search?device=${me.device_id}`)).json()).items[0].photo_id
  await page.goto(`/#/media/${id}`)
  await expect(page.getByRole('note').filter({ hasText: 'Metadata incomplete' })).toContainText('No location')
  // the device chip appears in the library filters
  await page.goto('/')
  await expect(page.getByRole('toolbar', { name: 'Filters' }).getByRole('button', { name: "My Pixel" })).toBeVisible()
})
