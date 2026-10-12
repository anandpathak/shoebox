import { expect, test } from '@playwright/test'

// Serial and ordered: cancelling needs photos that aren't archived yet.
test.describe.configure({ mode: 'serial' })

test('cancel from the phone stops after the items in progress', async ({ page }) => {
  await page.goto('/#/uploads')
  await page.getByLabel('What to archive').selectOption('all')
  await page.getByLabel(/Dry run/).check()
  await page.getByRole('button', { name: 'Count photos' }).click()
  await page.getByRole('button', { name: 'Cancel after the current items' }).click()
  await expect(page.getByRole('heading', { name: 'Cancelled' })).toBeVisible({ timeout: 30_000 })
  const done = Number((await page.getByText(/of 8 done/).textContent())!.match(/(\d+) of 8/)![1])
  expect(done).toBeLessThan(8)
})

test('start an archive on the Mac from a phone, reattach after a reload, second phone sees it', async ({ page, browser }) => {
  await page.goto('/#/uploads')
  const dialog = page.getByRole('dialog', { name: 'Name this device' })
  if (await dialog.isVisible()) { await dialog.getByRole('button', { name: 'Save' }).click() }
  await page.getByLabel('What to archive').selectOption('all')
  await page.getByRole('button', { name: 'Start archive' }).click()
  await expect(page.getByRole('heading', { name: 'Archiving…' })).toBeVisible()

  // the phone locks / the tab reloads: the page reattaches to the run in progress
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Archiving…' })).toBeVisible()

  // a second phone opens Uploads at the same time
  const other = await browser.newContext({ extraHTTPHeaders: { 'Tailscale-User-Login': 'phone-user@example.com' } })
  const p2 = await other.newPage()
  await p2.goto('/#/uploads')
  await expect(p2.getByRole('button', { name: 'A run is in progress' })).toBeDisabled()

  await expect(page.getByRole('heading', { name: 'Finished' })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/8 of 8 done/)).toBeVisible()           // counted once despite the reconnect
  await other.close()
})
