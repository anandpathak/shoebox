// "Choose photos" in the iPhone app: iOS's own picker (any number of photos), then the same native upload
// path as automatic backup, so the originals arrive with all their metadata (location included) and keep
// uploading in the background. Photos the archive already has are skipped.
import { api } from '../api/client'
import { getDevice } from '../lib/device'
import { pairToken } from '../lib/pairing'
import { nativePhone, plugin } from './native'

export interface Picked { id: string; filename: string; kind: 'photo' | 'video' }
export interface PickProgress {
  phase: 'reading' | 'checking' | 'sending' | 'done'; total: number; read: number; already: number; sent: number
  hashes: string[]; unavailable: number
}

type Picker = { pick(o: { limit: number }): Promise<{ assets: Picked[]; unavailable?: number }> }

/** True inside the iPhone app (its plugin has a native picker). */
export function canPickNatively(): boolean {
  const p = plugin() as unknown as Partial<Picker> | null
  return typeof p?.pick === 'function'
}

export async function pickAndUpload(onProgress: (p: PickProgress) => void): Promise<PickProgress | null> {
  const p = plugin()
  if (!p) return null
  const { assets, unavailable = 0 } = await (p as unknown as Picker).pick({ limit: 0 })
  if (!assets?.length && !unavailable) return null
  const phone = nativePhone(p)
  const st: PickProgress = { phase: 'reading', total: assets.length, read: 0, already: 0, sent: 0, hashes: [], unavailable }
  const hashed: { a: Picked; sha: string }[] = []
  for (const a of assets) {
    const { sha256 } = await phone.hash(a.id, true)          // downloads from iCloud if needed
    hashed.push({ a, sha: sha256 })
    st.read++
    onProgress({ ...st })
  }
  st.phase = 'checking'
  st.hashes = hashed.map((h) => h.sha)
  onProgress({ ...st })
  const missing = new Set<string>()
  for (let i = 0; i < hashed.length; i += 500) {
    const r = await api<{ missing: string[] }>('/api/backup/check', { method: 'POST', json: { hashes: st.hashes.slice(i, i + 500) } })
    r.missing.forEach((h) => missing.add(h))
  }
  st.phase = 'sending'
  const device = getDevice(), auth = pairToken()
  // the Mac only accepts uploads from a registered device (registering again is harmless)
  await api('/api/devices/register', { method: 'POST', json: { device_id: device.device_id, name: device.name, platform: device.platform } })
  const sent = new Set<string>()
  for (const { a, sha } of hashed) {
    if (!missing.has(sha) || sent.has(sha)) { st.already += missing.has(sha) ? 0 : 1; continue }
    await phone.upload({
      assetId: a.id, url: `${location.origin}/api/uploads/raw`, sha256: sha, wifiOnly: false,
      headers: { ...(auth ? { Authorization: `Bearer ${auth}` } : {}), 'X-Device-Id': device.device_id, 'X-Asset-Id': a.id,
                 'X-Sha256': sha, 'X-Filename': a.filename },
    })
    sent.add(sha)
    st.sent++
    onProgress({ ...st })
  }
  st.phase = 'done'
  onProgress({ ...st })
  return st
}

/** How many of these files the Mac has archived (or received and is archiving). */
export async function archivedCount(hashes: string[]): Promise<number> {
  let n = 0
  for (let i = 0; i < hashes.length; i += 500) {
    const r = await api<{ archived: string[]; in_progress: string[] }>('/api/backup/check', { method: 'POST', json: { hashes: hashes.slice(i, i + 500) } })
    n += r.archived.length + r.in_progress.length
  }
  return n
}
