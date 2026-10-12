import { test as base, devices, webkit, type BrowserContext, type Page } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// WebKit test contexts behave like Private Browsing, where Safari refuses on-device (OPFS) storage.
// An installed app on a real iPhone has normal storage, so WebKit tests share one persistent profile
// (like one installed app) and clear its data between tests. Safari also allows only one page at a
// time to hold the storage, which the app guards with a Web Lock.
export const test = base.extend<{ devicePage: Page }, { webkitApp: BrowserContext | null }>({
  webkitApp: [async ({ browserName }, use) => {
    if (browserName !== 'webkit') return use(null)
    const ctx = await webkit.launchPersistentContext(mkdtempSync(join(tmpdir(), 'photoctl-wk-')), { ...devices['iPhone 15'] })
    await use(ctx)
    await ctx.close()
  }, { scope: 'worker' }],
  devicePage: async ({ page, webkitApp, baseURL }, use) => {
    if (!webkitApp) return use(page)
    const p = await webkitApp.newPage()
    const goto = p.goto.bind(p)
    p.goto = (url, opts) => goto(new URL(url, baseURL).toString(), opts)
    await use(p)
    await p.evaluate(async () => {           // forget everything this test stored, then let go of the storage
      const { local } = (globalThis as unknown as { photoctlLocal: { local: { call: (o: string, ...a: unknown[]) => Promise<unknown>; release: () => Promise<unknown> } } }).photoctlLocal
      await local.call('remove', 'library').catch(() => {})
      await local.release()
      localStorage.clear()
    }).catch(() => {})
    await p.close({ runBeforeUnload: true })
  },
})
export { expect } from '@playwright/test'

export type LocalApi = { call: <T = unknown>(op: string, ...a: unknown[]) => Promise<T> }
export async function localCall<T>(page: Page, op: string, ...args: unknown[]): Promise<T> {
  return page.evaluate(([o, a]) => (globalThis as unknown as { photoctlLocal: { local: LocalApi } })
    .photoctlLocal.local.call(o as string, ...(a as unknown[])), [op, args] as const) as Promise<T>
}
