// Other devices' catalogs, downloaded to search on this one (LOCAL_APP_PLAN.md §11.3).
import { gunzip, local } from './client'
import { downloadResumable } from './download'
import { getLocal, setLocal } from './state'

export interface Published {
  device_id: string; name: string; platform: string; item_count: number; contributed_count?: number
  created_at: string; bytes?: number; kind: 'library' | 'published'
}

export const dbName = (deviceId: string) => `dev-${deviceId.replace(/[^0-9A-Za-z_-]/g, '_')}`

export async function downloadDeviceCatalog(c: Published, onProgress?: (got: number, total: number) => void) {
  const { bytes } = await downloadResumable(`/api/catalogs/${encodeURIComponent(c.device_id)}`, onProgress)
  const name = dbName(c.device_id)
  const { items } = await local.call<{ items: number }>('importCatalog', name, await gunzip(bytes.buffer as ArrayBuffer))
  const st = getLocal()
  setLocal({
    devices: { ...(st.devices ?? {}), [c.device_id]: { name: c.name, platform: c.platform, items,
                                                       downloadedAt: new Date().toISOString(), createdAt: c.created_at } },
    open: [...new Set([...(st.open ?? (st.enabled ? ['library'] : [])), name])],
  })
}

export async function removeDeviceCatalog(deviceId: string) {
  const name = dbName(deviceId)
  await local.call('remove', name)
  const st = getLocal()
  const devices = { ...(st.devices ?? {}) }
  delete devices[deviceId]
  setLocal({ devices, open: (st.open ?? []).filter((n) => n !== name) })
}

export function setOpen(name: string, on: boolean) {
  const st = getLocal()
  const open = new Set(st.open ?? (st.enabled ? ['library'] : []))
  if (on) open.add(name)
  else open.delete(name)
  setLocal({ open: [...open] })
}
