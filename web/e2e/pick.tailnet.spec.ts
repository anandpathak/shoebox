import { expect, test } from '@playwright/test'

// The iPhone app's native picker (stand-in): any number of items, uploaded like automatic backup.
test('the iPhone app picks several photos natively and they are archived', async ({ page }) => {
  await page.addInitScript(() => {
    const seed = Math.random().toString(36).slice(2)
    const jpeg = async (hue: number) => {
      const c = new OffscreenCanvas(64, 48); const g = c.getContext('2d')!
      g.fillStyle = `hsl(${hue} 70% 50%)`; g.fillRect(0, 0, 64, 48); g.fillStyle = '#fff'; g.fillText(seed, 2, 24)
      return c.convertToBlob({ type: 'image/jpeg' })
    }
    const items = [{ id: 'A1', hue: 20 }, { id: 'A2', hue: 140 }, { id: 'A3', hue: 260 }]
    const blobs = new Map(items.map((i) => [i.id, jpeg(i.hue)]))
    const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, '0')).join('')
    ;(globalThis as Record<string, unknown>).Capacitor = {
      isNativePlatform: () => true, getPlatform: () => 'ios',
      Plugins: { PhotoBackup: {
        pick: async () => ({ assets: items.map((i) => ({ id: i.id, filename: `IMG_${i.id}.JPG`, kind: 'photo' })), unavailable: 0 }),
        hash: async ({ id }: { id: string }) => {
          const b = await blobs.get(id)!
          return { sha256: hex(await crypto.subtle.digest('SHA-256', await b.arrayBuffer())), bytes: b.size }
        },
        upload: async (req: { assetId: string; url: string; headers: Record<string, string> }) => {
          await fetch(req.url, { method: 'POST', headers: { ...req.headers, 'Content-Type': 'image/jpeg' }, body: await blobs.get(req.assetId)! })
        },
        access: async () => ({ access: 'full' }), uploads: async () => ({ uploads: [] }), forget: async () => {},
        changes: async () => ({ assets: [], deleted: [], token: 't', full: true }), configure: async () => {},
        conditions: async () => ({ online: true, wifi: true, charging: true }),
        addListener: async () => ({ remove() {} }), getViewerAccess: async () => ({ json: null }),
      } },
    }
  })
  await page.goto('/#/uploads')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  if (await dialog.isVisible()) await dialog.getByRole('button', { name: 'Save' }).click()
  const card = page.getByRole('region', { name: /From this/ })
  await card.getByRole('button', { name: 'Choose photos and videos' }).click()
  await expect(card.getByTestId('pick-status')).toContainText('3 sent', { timeout: 30_000 })
  await expect(card.getByTestId('pick-status')).toContainText('3 of 3 archived', { timeout: 60_000 })
  // choosing the same ones again sends nothing
  await card.getByRole('button', { name: 'Choose photos and videos' }).click()
  await expect(card.getByTestId('pick-status')).toContainText('0 sent · 3 already in your archive', { timeout: 30_000 })
})
