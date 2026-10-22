import { expect, test } from '@playwright/test'

test('Uploads shows this device first; the Mac\'s library is folded away on phones', async ({ page }, info) => {
  test.skip(!['iphone', 'desktop'].includes(info.project.name), 'a phone and the Mac')
  await page.goto('/#/uploads')
  const headings = page.getByRole('heading', { level: 2 })
  if (info.project.name === 'iphone') {
    await expect(headings.first()).toHaveText('From this iPhone')
    const mac = page.getByTestId('mac-section')
    await expect(mac).not.toHaveAttribute('open', /.*/)
    await expect(page.getByLabel('What to archive')).toBeHidden()
    await mac.locator('summary').click()
    await expect(page.getByLabel('What to archive')).toBeVisible()
    expect(await page.getByLabel('Choose photos and videos').getAttribute('multiple')).not.toBeNull()
  } else {                                         // a browser on the Mac itself
    await expect(headings.first()).toHaveText(/Archive from Apple Photos on/)
    await expect(page.getByRole('heading', { name: 'From this Mac' })).toBeVisible()
    await expect(page.getByTestId('mac-section')).toHaveCount(0)
  }
})
