// A phone publishes its on-device catalog so other devices can download it (LOCAL_APP_PLAN.md §11.1).
// The Mac checks it and stores it in S3 under this device's id.
import { api } from '../api/client'
import { getDevice, saveDevice } from '../lib/device'
import { local } from './client'
import { getLocal, setLocal } from './state'

const MIN_INTERVAL = 10 * 60 * 1000

export async function ensureRegistered() {
  const dev = getDevice()
  if (dev.registered) return dev
  await api('/api/devices/register', { method: 'POST', json: { device_id: dev.device_id, name: dev.name, platform: dev.platform } })
  const next = { ...dev, registered: true }
  saveDevice(next)
  return next
}

async function gzip(bytes: Uint8Array): Promise<Blob> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new CompressionStream('gzip'))
  return new Response(stream).blob()
}

export interface PublishResult { item_count: number; contributed_count: number; bytes: number }

/** Publish now, or (auto) only if it's been 10+ minutes since the last time. */
export async function publishLocal(force = false): Promise<PublishResult | null> {
  const st = getLocal()
  if (!st.enabled) return null
  if (!force && st.publishedAt && Date.now() - Date.parse(st.publishedAt) < MIN_INTERVAL) return null
  const dev = await ensureRegistered()
  const bytes = await local.call<Uint8Array>('exportCatalog', 'library')
  const body = await gzip(bytes)
  const res = await api<PublishResult>(`/api/catalogs/${encodeURIComponent(dev.device_id)}`,
    { method: 'PUT', body, headers: { 'Content-Type': 'application/gzip' } })
  setLocal({ publishedAt: new Date().toISOString() })
  return res
}
