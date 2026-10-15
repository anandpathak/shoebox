// This browser/phone's identity (LOCAL_APP_PLAN.md §9.4). Kept in localStorage; OPFS copy added with on-device mode.
const KEY = 'shoebox.device'
const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'

export interface LocalDevice { device_id: string; name: string; platform: 'ios' | 'android' | 'web'; registered?: boolean }

export function ulid(now = Date.now()): string {
  let t = now
  let time = ''
  for (let i = 0; i < 10; i++) { time = CROCKFORD[t % 32] + time; t = Math.floor(t / 32) }
  const rand = crypto.getRandomValues(new Uint8Array(16))
  let r = ''
  for (let i = 0; i < 16; i++) r += CROCKFORD[rand[i] % 32]
  return time + r
}

export function detectPlatform(ua = navigator.userAgent, touch = navigator.maxTouchPoints): LocalDevice['platform'] {
  if (/android/i.test(ua)) return 'android'
  if (/iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && touch > 1)) return 'ios'
  return 'web'
}

export function defaultName(platform: LocalDevice['platform']): string {
  return platform === 'ios' ? 'iPhone' : platform === 'android' ? 'Android phone' : 'Browser'
}

export function getDevice(): LocalDevice {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved) return JSON.parse(saved)
  } catch { /* private mode: fall through */ }
  const platform = detectPlatform()
  const dev: LocalDevice = { device_id: ulid(), name: defaultName(platform), platform }
  saveDevice(dev)
  return dev
}

export function saveDevice(dev: LocalDevice) {
  try { localStorage.setItem(KEY, JSON.stringify(dev)) } catch { /* ignore */ }
}
