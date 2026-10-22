import { expect, test } from '@playwright/test'

test.describe.configure({ mode: 'serial' })

test('edit title, tags and favourite; they persist', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'one project is enough for a shared server')
  await page.goto('/')
  const tile = page.locator('.tile').nth(3)
  const id = await tile.getAttribute('data-id')
  await tile.click()
  const v = page.getByRole('dialog', { name: 'Photo viewer' })
  await page.getByRole('button', { name: 'Details' }).click()                 // the sheet with title, tags, info
  await v.getByLabel('Title').fill('Sunset at Kudle')
  await v.getByLabel('Title').press('Enter')
  await v.getByLabel('Add tag').fill('Golden hour')
  await v.getByLabel('Add tag').press('Enter')
  await expect(v.getByLabel('Tags')).toContainText('golden hour', { timeout: 15_000 })
  await v.getByRole('button', { name: 'Add to favourites' }).click()            // in the action bar
  await expect(v.getByRole('button', { name: 'Favourite', exact: true })).toHaveAttribute('aria-pressed', 'true')

  await page.reload()
  await page.getByRole('button', { name: 'Details' }).click()                 // the sheet with title, tags, info
  await expect(v.getByLabel('Title')).toHaveValue('Sunset at Kudle')
  await expect(v.getByLabel('Tags')).toContainText('golden hour', { timeout: 15_000 })
  await v.getByRole('button', { name: 'Remove tag golden hour' }).click()
  await expect(v.getByLabel('Tags')).not.toContainText('golden hour')
  const detail = await (await page.request.get(`/api/media/${id}`)).json()
  expect(detail.title).toBe('Sunset at Kudle')
  expect(detail.favorite).toBe(1)
  await page.keyboard.press('Escape')
  await page.getByLabel('Search the library').fill('kudle')
  await expect(page.locator('.tile')).toHaveCount(1)
})

test('remove, then undo', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'one project is enough for a shared server')
  await page.goto('/')
  const tile = page.locator('.tile').nth(5)
  const id = await tile.getAttribute('data-id')
  await tile.click()
  const v = page.getByRole('dialog', { name: 'Photo viewer' })
  await v.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(v.getByText('Removed from your library')).toBeVisible()
  expect((await (await page.request.get(`/api/media/${id}`)).json()).deleted).toBe(1)
  await v.getByRole('button', { name: 'Undo' }).click()
  await expect(v.getByRole('button', { name: 'Remove', exact: true })).toBeVisible()
  expect((await (await page.request.get(`/api/media/${id}`)).json()).deleted).toBe(0)
})
