import { expect, localCall, test } from './fixtures'

test('changes made on the Mac reach the on-device catalog with one refresh', async ({ devicePage: page }) => {
  await page.goto('/#/catalogs')
  await page.getByRole('button', { name: 'Use on this device' }).click()
  await expect(page.getByText('On this device', { exact: true })).toBeVisible({ timeout: 30_000 })

  // an edit and a newly archived item, made on the Mac after the download
  const first = (await (await page.request.get('/api/search?limit=1')).json()).items[0].photo_id
  const res = await page.request.patch(`/api/media/${first}`, { data: { title: 'Renamed on the Mac' },
                                       headers: { Origin: new URL(page.url()).origin } })
  expect(res.ok()).toBe(true)

  await page.getByRole('button', { name: 'Refresh now' }).click()
  await expect(page.getByText(/Applied \d+ changes/)).toBeVisible({ timeout: 20_000 })
  const hits = await localCall<{ items: { photo_id: string }[] }>(page, 'search', 'library', { q: 'renamed mac' })
  expect(hits.items.map((i) => i.photo_id)).toEqual([first])
  await page.getByRole('button', { name: 'Refresh now' }).click()
  await expect(page.getByText('Already up to date.')).toBeVisible()
})
