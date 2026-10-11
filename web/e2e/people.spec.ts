import { expect, test } from '@playwright/test'

test('rename, open and merge people', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'edits a shared server')
  await page.goto('/#/people')
  await expect(page.getByText('Asha', { exact: true })).toBeVisible()
  const kid = page.locator('.person-card', { hasText: 'Kid' })
  await kid.getByRole('button', { name: 'Rename' }).click()
  await kid.getByLabel('New name for Kid').fill('Little one')
  await kid.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Little one', { exact: true })).toBeVisible()

  const before = (await (await page.request.get('/api/search?person=Little%20one&limit=500')).json()).items.length
  const grandma = page.locator('.person-card', { hasText: 'Grandma' })
  await grandma.getByRole('button', { name: 'Merge…' }).click()
  const people = await (await page.request.get('/api/people')).json()
  const target = people.find((p: { name: string }) => p.name === 'Little one').person_id
  await grandma.getByLabel('Merge Grandma into').selectOption(target)
  await grandma.getByRole('button', { name: 'Merge', exact: true }).click()
  await expect(page.locator('.person-card', { hasText: 'Grandma' })).toHaveCount(0)
  const after = (await (await page.request.get('/api/search?person=Little%20one&limit=500')).json()).items.length
  expect(after).toBeGreaterThan(before)

  await page.locator('.person-card', { hasText: 'Little one' }).getByRole('button', { name: /Photos of/ }).click()
  await expect(page).toHaveURL(/person=Little/)
  await expect(page.locator('.tile')).toHaveCount(Math.min(after, 120))
})
