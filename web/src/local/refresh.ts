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

/** Send edits made on this device to the Mac (it relays them to S3 under this device's id). */
export async function pushOutbox(): Promise<number> {
  const { getDevice } = await import('../lib/device')
  const dev = getDevice()
  let sent = 0
  for (let i = 0; i < 100; i++) {
    const ops = await local.call<Op[]>('outbox', 'library', 500)
    if (!ops.length) break
    if (!dev.registered) {
      await api('/api/devices/register', { method: 'POST', json: { device_id: dev.device_id, name: dev.name, platform: dev.platform } })
    }
    await api('/api/sync/ops', { method: 'POST', json: { device_id: dev.device_id, ops } })
    await local.call('clearOutbox', 'library', ops.map((o) => o.op_id))
    sent += ops.length
  }
  return sent
}

async function run(redownload: () => Promise<unknown>): Promise<number> {
  const st = getLocal()
  if (!st.enabled) return 0
  await pushOutbox()
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
