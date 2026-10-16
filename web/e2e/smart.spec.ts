import { expect, test } from '@playwright/test'

// The test server has stand-in models (scripts/e2e_server.py fake_ai): vectors from each photo's labels.
test('plain-English search ranks by meaning; newest-first switches back', async ({ page }, info) => {
  test.skip(info.project.name === 'webkit-iphone', 'server search only')
  await page.goto('/#/library?q=snow%20mountains')
  await expect(page.getByText(/Showing best matches for/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Best matches' })).toHaveAttribute('aria-pressed', 'true')
  const best = (await (await page.request.get('/api/search/smart?q=snow%20mountains&limit=20')).json())
  expect(best.mode).toBe('smart')
  await expect(page.locator('.tile').first()).toBeVisible()
  const tagged = (await (await page.request.get('/api/search?q=snow')).json()).items.map((i: { photo_id: string }) => i.photo_id)
  expect(tagged).toContain(best.items[0].photo_id)                 // the best match really is a snowy one

  await page.getByRole('button', { name: 'Newest first' }).click()
  await expect(page).toHaveURL(/order=new/)
  await expect(page.getByText(/Showing best matches/)).toHaveCount(0)
})

test('a name in the query becomes a filter', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'one is enough')
  await page.goto('/')                                             // signs the browser in (cookie)
  const r = await (await page.request.get('/api/search/smart?q=asha%20at%20the%20beach')).json()
  expect(r.people).toEqual(['Asha'])
  await page.goto('/#/library?q=asha%20at%20the%20beach')
  await expect(page.getByText(/with Asha/)).toBeVisible()
})

test('captions show in the viewer, are searchable, and can be corrected', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'edits a shared server')
  await page.goto('/')
  const hit = (await (await page.request.get('/api/search?q=lagoon')).json()).items
  expect(hit.length).toBeGreaterThan(0)
  await page.goto(`/#/media/${hit[0].photo_id}`)
  await page.getByRole('button', { name: 'Details' }).click()
  await expect(page.getByTestId('caption')).toContainText('Palm trees by a quiet lagoon')
  await page.getByRole('button', { name: 'Edit caption' }).click()
  await page.getByLabel('Caption').fill('Our lagoon at Zephyrcove')
  await page.locator('.caption-edit').getByRole('button', { name: 'Save' }).click()
  await expect(page.getByTestId('caption')).toContainText('Zephyrcove')
  await expect.poll(async () => (await (await page.request.get('/api/search?q=zephyrcove')).json()).items.length).toBe(1)
})
