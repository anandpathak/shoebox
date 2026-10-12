// The `PhotoBackup` plugin from the native app (app/ios, app/android). In a browser there is none, and the
// app offers the assisted "Back up recent photos" picker instead (§13.5).
import type { Phone, Settings } from './types'

interface CapacitorGlobal {
  isNativePlatform?: () => boolean
  getPlatform?: () => string
  Plugins?: Record<string, unknown>
}

type Plugin = {
  access(): Promise<{ access: Awaited<ReturnType<Phone['access']>> }>
  requestAccess(): Promise<{ access: Awaited<ReturnType<Phone['access']>> }>
  changes(o: { token: string | null }): Promise<Awaited<ReturnType<Phone['changes']>>>
  hash(o: { id: string; allowNetwork: boolean }): ReturnType<Phone['hash']>
  upload(o: Parameters<Phone['upload']>[0]): Promise<void>
  uploads(): Promise<{ uploads: Awaited<ReturnType<Phone['uploads']>> }>
  forget(o: { ids: string[] }): Promise<void>
  conditions(): ReturnType<Phone['conditions']>
  addListener(event: 'backgroundRun' | 'uploadFinished', fn: () => void): Promise<{ remove(): void }>
  finishBackground(): Promise<void>
  configure(o: { origin: string; deviceId: string; auth: string | null } & Settings): Promise<void>
}

function cap(): CapacitorGlobal | undefined {
  return (globalThis as { Capacitor?: CapacitorGlobal }).Capacitor
}

export function nativePlatform(): 'ios' | 'android' | null {
  const c = cap()
  if (!c?.isNativePlatform?.() || !c.Plugins?.PhotoBackup) return null
  const p = c.getPlatform?.()
  return p === 'ios' || p === 'android' ? p : null
}

export function plugin(): Plugin | null {
  return nativePlatform() ? cap()!.Plugins!.PhotoBackup as Plugin : null
}

export function nativePhone(p: Plugin): Phone {
  return {
    access: async () => (await p.access()).access,
    requestAccess: async () => (await p.requestAccess()).access,
    changes: (token) => p.changes({ token }),
    hash: (id, allowNetwork) => p.hash({ id, allowNetwork }),
    upload: (req) => p.upload(req),
    uploads: async () => (await p.uploads()).uploads,
    forget: (ids) => p.forget({ ids }),
    conditions: () => p.conditions(),
  }
}

/** Back to the app's own start page (to scan or type another Mac's address). */
export function startPageUrl(): string | null {
  const p = nativePlatform()
  if (!p) return null
  return `${p === 'ios' ? 'capacitor://localhost' : 'https://localhost'}/index.html?change=1`
}
