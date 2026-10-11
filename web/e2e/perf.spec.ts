import { expect, test } from '@playwright/test'

// Only meaningful with a big library: E2E_ITEMS=10000 npx playwright test perf --project pixel
test('scrolling a large library stays smooth', async ({ page }) => {
  test.skip(Number(process.env.E2E_ITEMS ?? 0) < 5000, 'set E2E_ITEMS=10000 to run')
  test.setTimeout(180_000)
  await page.goto('/')
  await expect(page.locator('.tile').first()).toBeVisible()
  // load everything by scrolling to the end repeatedly
  for (let i = 0; i < 200; i++) {
    const done = await page.locator('.sentinel').textContent()
    if (done?.includes('everything')) break
    await page.locator('.sentinel').scrollIntoViewIfNeeded()
    await page.waitForTimeout(150)
  }
  const count = await page.locator('.tile').count()
  expect(count).toBeGreaterThanOrEqual(Number(process.env.E2E_ITEMS))
  // now measure frame gaps while scrolling from top to bottom
  const gaps = await page.evaluate(async () => {
    window.scrollTo(0, 0)
    const out: number[] = []
    let last = performance.now()
    const end = document.documentElement.scrollHeight
    await new Promise<void>((resolve) => {
      const step = () => {
        const now = performance.now()
        out.push(now - last)
        last = now
        window.scrollBy(0, 400)
        if (window.scrollY + innerHeight >= end - 10) resolve()
        else requestAnimationFrame(step)
      }
      requestAnimationFrame(step)
    })
    return out
  })
  gaps.sort((a, b) => a - b)
  const p95 = gaps[Math.floor(gaps.length * 0.95)]
  console.log(`tiles=${count} frames=${gaps.length} p95 frame=${p95.toFixed(1)}ms`)
  expect(p95).toBeLessThan(50)
})
