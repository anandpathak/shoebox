// Runs backup passes one at a time: on app open, on pull-to-refresh / sync, when the OS wakes the app
// (iOS BGTask, Android WorkManager) and when an upload finishes.
import { getDevice } from '../lib/device'
import { pairToken } from '../lib/pairing'
import { runBackup } from './engine'
import { retryFailed, status as summarise } from './ledger'
import { nativePhone, nativePlatform, plugin } from './native'
import { loadLedger, loadSettings, saveLedger, saveSettings } from './store'
import type { Settings, Status } from './types'

let current: Promise<Status> | null = null
let again = false
const listeners = new Set<(s: Status) => void>()

export function subscribe(fn: (s: Status) => void): () => void { listeners.add(fn); return () => listeners.delete(fn) }
const emit = (s: Status) => listeners.forEach((l) => l(s))

export const available = () => nativePlatform() !== null

export async function currentStatus(): Promise<Status | null> {
  const p = plugin()
  if (!p) return null
  const [ledger, settings, access] = await Promise.all([loadLedger(), loadSettings(), nativePhone(p).access()])
  return summarise(ledger, settings.enabled, access, null)
}

/** One pass; a request while one is running queues exactly one more. */
export function backupNow(): Promise<Status | null> {
  const p = plugin(), platform = nativePlatform()
  if (!p || !platform) return Promise.resolve(null)
  if (current) { again = true; return current }
  current = (async () => {
    try {
      let s: Status
      do {
        again = false
        const device = getDevice()
        const settings = await loadSettings()
        // Background passes (app closed) need the same origin, device and settings.
        await p.configure({ origin: location.origin, deviceId: device.device_id, auth: pairToken(), ...settings }).catch(() => {})
        s = await runBackup({
          phone: nativePhone(p), load: loadLedger, save: saveLedger, fetch: (...a) => fetch(...a),
          origin: location.origin, deviceId: device.device_id, platform, settings, onProgress: emit, auth: pairToken(),
        })
      } while (again)
      return s
    } finally { current = null }
  })()
  return current
}

export async function updateSettings(change: Partial<Settings>): Promise<Settings> {
  const next = { ...(await loadSettings()), ...change }
  await saveSettings(next)
  await plugin()?.configure({ origin: location.origin, deviceId: getDevice().device_id, auth: pairToken(), ...next }).catch(() => {})
  if (next.enabled) {
    const p = plugin()
    if (p && change.enabled) await nativePhone(p).requestAccess()
    void backupNow()
  } else {
    const s = await currentStatus()
    if (s) emit(s)
  }
  return next
}

export async function retryFailedNow(): Promise<void> {
  await saveLedger(retryFailed(await loadLedger()))
  void backupNow()
}

/** Wire up the native triggers once, when the app starts. */
export function startAutomaticBackup(): void {
  const p = plugin()
  if (!p) return
  void backupNow()
  p.addListener('uploadFinished', () => { void backupNow() })
  p.addListener('backgroundRun', () => { void backupNow().finally(() => p.finishBackground()) })
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void backupNow() })
}
