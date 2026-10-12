import { expect, test } from '@playwright/test'

// The native app's PhotoBackup plugin, faked in the page: a small camera roll of real JPEGs, and an OS
// upload queue that really POSTs to the Mac. Everything else (engine, ledger, server) is the real thing.
function installFakePhone() {
  type Up = { assetId: string; state: string; status?: number; body?: string; error?: string }
  const listeners: Record<string, (() => void)[]> = {}
  const seed = Math.random().toString(36).slice(2)
  async function jpeg(hue: number): Promise<Blob> {
    const c = new OffscreenCanvas(64, 48)
    const g = c.getContext('2d')!
    g.fillStyle = `hsl(${hue} 70% 50%)`; g.fillRect(0, 0, 64, 48)
    g.fillStyle = '#fff'; g.fillText(seed, 2, 24)
    return c.convertToBlob({ type: 'image/jpeg', quality: 0.9 })
  }
  const now = Date.now()
  const roll = [
    { id: '1001', hue: 10, filename: 'IMG_1001.jpg' },
    { id: '1002', hue: 120, filename: 'IMG_1002.jpg' },
    { id: '1003', hue: 10, filename: 'IMG_1003.jpg' },                 // the same bytes as 1001
    { id: '1004', hue: 240, filename: 'Screenshot_1.jpg', screenshot: true },
  ]
  const blobs = new Map<string, Promise<Blob>>(roll.map((r) => [r.id, jpeg(r.hue)]))
  const uploads = new Map<string, Up>()
  const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, '0')).join('')
  const emit = (e: string) => (listeners[e] ?? []).forEach((f) => f())
  ;(globalThis as Record<string, unknown>).fakePhone = { uploads, roll }
  ;(globalThis as Record<string, unknown>).Capacitor = {
    isNativePlatform: () => true,
    getPlatform: () => 'android',
    Plugins: { PhotoBackup: {
      access: async () => ({ access: 'full' }),
      requestAccess: async () => ({ access: 'full' }),
      changes: async ({ token }: { token: string | null }) => ({
        assets: token ? [] : roll.map((r) => ({ id: r.id, modified: 1, created: now, kind: 'photo', filename: r.filename,
                                                bytes: null, screenshot: !!r.screenshot, albums: ['Camera'] })),
        deleted: [], token: 'gen-1', full: !token }),
      hash: async ({ id }: { id: string }) => {
        const b = await blobs.get(id)!
        return { sha256: hex(await crypto.subtle.digest('SHA-256', await b.arrayBuffer())), bytes: b.size }
      },
      upload: async (req: { assetId: string; url: string; headers: Record<string, string> }) => {
        const u: Up = { assetId: req.assetId, state: 'queued' }
        uploads.set(req.assetId, u)
        setTimeout(async () => {
          try {
            const r = await fetch(req.url, { method: 'POST', headers: { ...req.headers, 'Content-Type': 'image/jpeg' },
                                             body: await blobs.get(req.assetId)! })
            Object.assign(u, { state: 'done', status: r.status, body: await r.text() })
          } catch (e) { Object.assign(u, { state: 'failed', error: String(e) }) }
          emit('uploadFinished')
        }, 50)
      },
      uploads: async () => ({ uploads: [...uploads.values()] }),
      forget: async ({ ids }: { ids: string[] }) => { ids.forEach((i) => uploads.delete(i)) },
      conditions: async () => ({ online: true, wifi: true, charging: true }),
      configure: async () => {},
      finishBackground: async () => {},
      addListener: async (e: string, f: () => void) => { (listeners[e] ??= []).push(f); return { remove() {} } },
    } },
  }
}

test('the phone app backs up its camera roll automatically, once per photo', async ({ page }) => {
  await page.addInitScript(installFakePhone)
  await page.goto('/#/sync')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  await dialog.getByRole('button', { name: 'Save' }).click()
  const card = page.getByRole('region', { name: 'Back up this phone' })
  await expect(card.getByTestId('backup-status')).toHaveText('Automatic backup is off.')
  await card.getByLabel('Back up automatically').check()

  // 1001 and 1002 are sent; 1003 has the same bytes as 1001; the screenshot is left out by default
  await expect(card.getByTestId('backup-status')).toHaveText('Backed up 3 of 3', { timeout: 30_000 })
  await expect(card.getByText(/1 left out by the settings below/)).toBeVisible()
  const sent = await page.evaluate(() => [...(globalThis as unknown as { fakePhone: { uploads: Map<string, unknown> } }).fakePhone.uploads.keys()])
  expect(sent).toEqual([])                                     // finished uploads are tidied away

  const device = await page.evaluate(() => JSON.parse(localStorage.getItem('photoctl.device')!).device_id as string)
  await expect.poll(async () => (await (await page.request.get(`/api/backup/status?device=${device}`)).json()).archived,
                    { timeout: 30_000 }).toBe(2)
  const items = (await (await page.request.get(`/api/search?device=${device}`)).json()).items
  const refs = await Promise.all(items.map(async (i: { photo_id: string }) =>
    (await (await page.request.get(`/api/media/${i.photo_id}`)).json()).sources.map((s: { source_ref: string }) => s.source_ref)))
  expect(refs.flat().sort()).toEqual(['android:1001', 'android:1002'])

  // screenshots on: only the new one is sent
  await card.getByLabel('Include screenshots').check()
  await expect(card.getByTestId('backup-status')).toHaveText('Backed up 4 of 4', { timeout: 30_000 })
  await expect.poll(async () => (await (await page.request.get(`/api/backup/status?device=${device}`)).json()).archived,
                    { timeout: 30_000 }).toBe(3)
})
