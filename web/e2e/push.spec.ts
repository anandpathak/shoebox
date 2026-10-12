import { expect, test } from '@playwright/test'

test('turn on notifications subscribes this device', async ({ page, context }, info) => {
  test.skip(info.project.name !== 'desktop', 'Chrome desktop has push without installing')
  await context.grantPermissions(['notifications'])
  await page.goto('/#/sync')
  await expect.poll(() => page.evaluate(async () => !!(await navigator.serviceWorker.ready).active)).toBe(true)
  const card = page.locator('.card', { has: page.getByRole('heading', { name: 'Notifications' }) })
  await card.getByRole('button', { name: 'Turn on notifications' }).click()
  // Chrome talks to Google's push service to subscribe; offline CI can't, so accept either outcome but no crash
  await expect(card.getByText('On for this device').or(card.locator('.error'))).toBeVisible({ timeout: 20_000 })
})
