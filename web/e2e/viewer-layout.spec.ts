import { expect, test } from '@playwright/test'

type Box = { x: number; y: number; width: number; height: number }
const overlaps = (a: Box, b: Box) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

// The photo sits between the top bar and the action bar; details open in a sheet over it and close again.
test('viewer: photo never under the bars; details sheet opens and closes', async ({ page }, info) => {
  test.skip(info.project.name !== 'iphone' && info.project.name !== 'desktop', 'phone and desktop')
  await page.goto('/')
  await page.locator('.tile').first().click()
  const v = page.getByRole('dialog', { name: 'Photo viewer' })
  const img = v.locator('.viewer-media img')
  await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true)
  // the visible picture (object-fit: contain) inside the image box
  const pic = await img.evaluate((i: HTMLImageElement) => {
    const r = i.getBoundingClientRect(), s = Math.min(r.width / i.naturalWidth, r.height / i.naturalHeight)
    const w = i.naturalWidth * s, h = i.naturalHeight * s
    return { x: r.x + (r.width - w) / 2, y: r.y + (r.height - h) / 2, width: w, height: h }
  })
  const [top, bar] = await Promise.all([v.locator('.vtop').boundingBox(), v.locator('.vbar').boundingBox()])
  expect(overlaps(pic, top!)).toBe(false)
  expect(overlaps(pic, bar!)).toBe(false)
  const vw = page.viewportSize()!.width
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(vw)

  const actions = v.getByRole('navigation', { name: 'Photo actions' })
  await expect(actions.getByRole('button', { name: 'Details' })).toBeVisible()
  await expect(actions.getByRole('link', { name: /Download photo/ })).toBeVisible()
  const sheet = page.locator('.sheet')                                   // hidden from screen readers while closed
  await expect(sheet).toHaveAttribute('data-open', 'false')
  await actions.getByRole('button', { name: 'Details' }).click()
  await expect(sheet).toHaveAttribute('data-open', 'true')
  await expect(sheet.getByLabel('Title')).toBeVisible()
  await expect(sheet.getByText('About this photo')).toBeVisible()
  if (info.project.name === 'iphone') {
    await page.mouse.click(vw / 2, 40)                                 // tap the dimmed photo above the sheet
  } else {
    await expect(actions.getByRole('button', { name: 'Remove' })).toBeVisible()   // the panel sits beside, not over
    await page.keyboard.press('Escape')
  }
  await expect(sheet).toHaveAttribute('data-open', 'false')

  if (info.project.name === 'iphone') {                                 // a tap on the photo hides the bars
    await img.click()
    await expect(v).toHaveAttribute('data-chrome', 'off')
    await img.click()
    await expect(v).toHaveAttribute('data-chrome', 'on')
  }
})
