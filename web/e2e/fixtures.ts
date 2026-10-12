import { test as base, devices, webkit, type BrowserContext, type Page } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// WebKit test contexts behave like Private Browsing, where Safari refuses on-device (OPFS) storage.
// An installed app on a real iPhone has normal storage, so WebKit tests share one persistent profile
// (like one installed app) and clear its data between tests. Safari also allows only one page at a
// time to hold the storage, which the app guards with a Web Lock.
// Playwright's WebKit can keep a closed or reloaded page's worker alive, still holding the storage, so
// WebKit tests share one page (like one running app) and reset its data between tests. Reopening the app
// on a real iPhone is a QA_CHECKLIST item.
export const test = base.extend<{ devicePage: Page }, { webkitApp: { ctx: BrowserContext; page: Page } | null }>({
  webkitApp: [async ({ browserName }, use) => {
    if (browserName !== 'webkit') return use(null)
    const ctx = await webkit.launchPersistentContext(mkdtempSync(join(tmpdir(), 'photoctl-wk-')), { ...devices['iPhone 15'] })
    await use({ ctx, page: ctx.pages()[0] ?? await ctx.newPage() })
    await ctx.close()
  }, { scope: 'worker' }],
  devicePage: async ({ page, webkitApp, baseURL }, use) => {
    if (!webkitApp) return use(page)
    const p = webkitApp.page
    if (!(p as Page & { __patched?: boolean }).__patched) {
      const goto = p.goto.bind(p)
      p.goto = (url, opts) => goto(new URL(url, baseURL).toString(), opts)
      ;(p as Page & { __patched?: boolean }).__patched = true
    }
    await use(p)
    await webkitApp.ctx.setOffline(false)
    await p.evaluate(async () => {           // forget everything this test stored
      const { local } = (globalThis as unknown as { photoctlLocal: { local: { call: (o: string, ...a: unknown[]) => Promise<unknown> } } }).photoctlLocal
      await local.call('remove', 'library').catch(() => {})
      localStorage.clear()
    }).catch(() => {})
  },
})
export { expect } from '@playwright/test'

export type LocalApi = { call: <T = unknown>(op: string, ...a: unknown[]) => Promise<T> }
export async function localCall<T>(page: Page, op: string, ...args: unknown[]): Promise<T> {
  return page.evaluate(([o, a]) => (globalThis as unknown as { photoctlLocal: { local: LocalApi } })
    .photoctlLocal.local.call(o as string, ...(a as unknown[])), [op, args] as const) as Promise<T>
}
