// Keep the on-device catalog current: pull ops the server has seen since our cursor and apply them here
// with the same rules as the Mac (LOCAL_APP_PLAN.md §10.2). A 410 means the server's catalog was rebuilt.
import { ApiError, api } from '../api/client'
import type { Op } from './apply'
import { local } from './client'
import { getLocal, setLocal } from './state'

let running: Promise<number> | null = null

export function refreshLocal(redownload: () => Promise<unknown>): Promise<number> {
  if (!running) running = run(redownload).finally(() => { running = null })
  return running
}

async function run(redownload: () => Promise<unknown>): Promise<number> {
  const st = getLocal()
  if (!st.enabled) return 0
  let cursor = st.cursor
  let applied = 0
  for (let page = 0; page < 200; page++) {
    let res: { epoch: string; ops: Op[]; cursor: number; more: boolean }
    try {
      res = await api(`/api/sync/ops?since=${cursor}${st.epoch ? `&epoch=${encodeURIComponent(st.epoch)}` : ''}&limit=1000`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 410) { await redownload(); return -1 }
      throw e
    }
    if (res.ops.length) applied += await local.call<number>('applyOps', 'library', res.ops)
    cursor = res.cursor
    setLocal({ cursor, epoch: res.epoch, refreshedAt: new Date().toISOString() })
    if (!res.more) break
  }
  return applied
}
