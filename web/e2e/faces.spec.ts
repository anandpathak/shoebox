import { expect, test } from '@playwright/test'

test('name a group of faces, then find them by name', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'names people on a shared server')
  await page.goto('/#/people')
  const who = page.getByRole('region', { name: "Who's this?" })
  await expect(who.getByTestId('analysis-status')).toBeVisible()
  const groups = who.getByTestId('face-group')
  await expect(groups).toHaveCount(2)
  const first = groups.first()
  await expect(first.locator('img').first()).toHaveJSProperty('complete', true)
  const n = Number((await first.locator('.muted').first().textContent())!.split(' ')[0])
  await first.getByLabel(/Name for this person/).fill('Meera Facetest')
  await first.getByRole('button', { name: 'Save' }).click()
  await expect(groups).toHaveCount(1)
  await expect(page.locator('.person-card', { hasText: 'Meera Facetest' })).toBeVisible()

  await page.goto('/#/library')
  await page.getByRole('searchbox').fill('meera')
  await page.keyboard.press('Enter')
  await expect(page.locator('.tile')).toHaveCount(n)

  await page.goto('/#/people')
  await groups.first().getByRole('button', { name: 'Not a person' }).click()
  await expect(who.getByText('Every face found so far has a name.')).toBeVisible()
})
