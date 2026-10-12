import { expect, localCall, test } from './fixtures'

test('edits made offline on the phone reach the Mac; a later Mac edit to the same field wins', async ({ devicePage: page }) => {
  await page.goto('/#/catalogs')
  await page.getByRole('button', { name: 'Use on this device' }).click()
  await expect(page.getByText('On this device', { exact: true })).toBeVisible({ timeout: 30_000 })
  const items = (await (await page.request.get('/api/search?type=photo&limit=30')).json()).items
  const target = items.find((i: { favorite: number }) => !i.favorite).photo_id
  const origin = new URL(page.url()).origin

  await page.context().setOffline(true)
  await page.goto(`/#/media/${target}`).catch(() => {})
  await expect(page.getByRole('status').first()).toContainText('on-device catalog', { timeout: 40_000 })
  const v = page.getByRole('dialog', { name: 'Photo viewer' })
  await v.getByLabel('Title').fill('Edited offline')
  await v.getByLabel('Title').press('Enter')
  await v.getByRole('button', { name: 'Add to favourites' }).click()
  await expect(page.getByRole('status').first()).toContainText('2 edits waiting')
  const offline = await localCall<{ title: string; favorite: number }>(page, 'media', 'library', target)
  expect([offline.title, offline.favorite]).toEqual(['Edited offline', 1])

  // meanwhile, on the Mac, someone renames the same photo (later in time)
  await page.waitForTimeout(50)
  await page.context().setOffline(false)
  const mac = await page.request.patch(`/api/media/${target}`, { data: { title: 'Renamed on the Mac' }, headers: { Origin: origin } })
  expect(mac.ok()).toBe(true)

  // back online: the phone sends its edits and pulls the Mac's
  await page.goto('/#/catalogs')
  await page.getByRole('button', { name: 'Refresh now' }).click()
  await expect(page.getByText(/Applied \d+ changes|Already up to date/)).toBeVisible({ timeout: 20_000 })
  const server = await (await page.request.get(`/api/media/${target}`)).json()
  expect([server.title, server.favorite]).toEqual(['Renamed on the Mac', 1])      // later title wins, favourite kept
  const here = await localCall<{ title: string; favorite: number }>(page, 'media', 'library', target)
  expect([here.title, here.favorite]).toEqual(['Renamed on the Mac', 1])         // both sides agree
  expect(await localCall<number>(page, 'outboxCount', 'library')).toBe(0)
})
