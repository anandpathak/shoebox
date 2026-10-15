import { expect, test } from '@playwright/test'

// The phone app's "Use without your Mac" card, with a stand-in for the native secure storage.
test('the phone fetches the read-only key from the Mac and keeps it in secure storage', async ({ page }) => {
  await page.addInitScript(() => {
    const store: { json: string | null } = { json: null }
    ;(globalThis as Record<string, unknown>).secureStore = store
    ;(globalThis as Record<string, unknown>).Capacitor = {
      isNativePlatform: () => true, getPlatform: () => 'ios',
      Plugins: { PhotoBackup: {
        setViewerAccess: async ({ json }: { json: string }) => { store.json = json },
        getViewerAccess: async () => ({ json: store.json }),
        clearViewerAccess: async () => { store.json = null },
        access: async () => ({ access: 'full' }), uploads: async () => ({ uploads: [] }),
        addListener: async () => ({ remove() {} }), configure: async () => {}, conditions: async () => ({ online: true, wifi: true, charging: true }),
        changes: async () => ({ assets: [], deleted: [], token: 't', full: true }), forget: async () => {},
      } },
    }
  })
  await page.goto('/#/sync')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  if (await dialog.isVisible()) await dialog.getByRole('button', { name: 'Save' }).click()
  const card = page.getByRole('region', { name: 'Use without your Mac' })
  await card.getByRole('button', { name: 'Set up' }).click()
  await expect(card.getByTestId('without-mac-status')).toHaveText('Ready on this phone.')
  const stored = await page.evaluate(() => JSON.parse((globalThis as unknown as { secureStore: { json: string } }).secureStore.json))
  expect(stored.key_id).toBe('e2e-reader')
  expect(stored.viewer).toBe('viewer/library.sqlite.gz')
  expect(await page.evaluate(() => localStorage.getItem('shoebox.viewerAccess'))).toBeNull()   // not in plain storage
  await expect(card.getByRole('link', { name: 'Open read-only library' })).toHaveAttribute('href', 'capacitor://localhost/browse/index.html#/library')
})
