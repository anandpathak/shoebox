import { test as base, devices, webkit, type Page } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// WebKit test contexts behave like Private Browsing, where Safari refuses on-device (OPFS) storage.
// An installed app on a real iPhone has normal storage, so WebKit tests use a persistent profile.
export const test = base.extend<{ devicePage: Page }>({
  devicePage: async ({ page, browserName, baseURL }, use, info) => {
    if (browserName !== 'webkit') return use(page)
    const ctx = await webkit.launchPersistentContext(mkdtempSync(join(tmpdir(), 'photoctl-wk-')),
      { ...devices['iPhone 15'], baseURL })
    const p = await ctx.newPage()
    await use(p)
    await ctx.close()
    void info
  },
})
export { expect } from '@playwright/test'

export type LocalApi = { call: <T = unknown>(op: string, ...a: unknown[]) => Promise<T> }
export async function localCall<T>(page: Page, op: string, ...args: unknown[]): Promise<T> {
  return page.evaluate(([o, a]) => (globalThis as unknown as { photoctlLocal: { local: LocalApi } })
    .photoctlLocal.local.call(o as string, ...(a as unknown[])), [op, args] as const) as Promise<T>
}
