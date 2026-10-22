// App-wide state: which data source is in use, who we are, and connection/sync status.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import { canPair } from './pairing'
import type { Health, SyncStatus } from '../api/types'
import { LocalDataSource, ServerDataSource, type DataSource } from '../data/source'
import { enableOnDevice } from '../local/enable'
import { refreshLocal } from '../local/refresh'
import { getLocal, onLocal, type LocalState } from '../local/state'
import type { ViewerState } from '../cloud/source'

export interface ViewerInfo {
  status: 'loading' | 'ready' | 'missing' | 'error'
  progress?: [number, number]
  state?: ViewerState | null
  error?: string
  refresh: () => void
}

export interface AppState {
  source: DataSource
  localState: LocalState
  health: Health | null
  online: boolean
  authError: string | null
  needsPairing: boolean     // a phone on the same Wi-Fi that hasn't been paired yet
  sync: SyncStatus | null
  refreshSync: () => void
  version: number          // bumps after edits/syncs so screens reload
  changed: () => void
  viewer?: ViewerInfo      // viewer mode: browsing from S3 without the Mac (read-only)
}

export const Ctx = createContext<AppState | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const server = useMemo(() => new ServerDataSource(), [])
  const device = useMemo(() => new LocalDataSource(), [])
  const [localState, setLocalState] = useState(getLocal)
  useEffect(() => onLocal(setLocalState), [])
  const [health, setHealth] = useState<Health | null>(null)
  const [online, setOnline] = useState(true)
  const [authError, setAuthError] = useState<string | null>(null)
  const [needsPairing, setNeedsPairing] = useState(false)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [version, setVersion] = useState(0)

  const refreshSync = useCallback(() => {
    api<SyncStatus>('/api/sync/status').then(setSync).catch(() => {})
  }, [])

  useEffect(() => {
    let alive = true
    const check = () => api<Health>('/api/health')
      .then((h) => {
        // an answer to a request sent just before the network dropped can arrive afterwards: don't let it
        // say "online" when the device has no connection
        if (!alive || !navigator.onLine) return
        setHealth(h); setOnline(true); setAuthError(null); setNeedsPairing(false)
      })
      .catch((e) => {
        if (!alive) return
        if (e.status === 401 && canPair()) setNeedsPairing(true)
        else if (e.status === 401 || e.status === 403) setAuthError(e.message)
        else setOnline(false)
      })
    check()
    refreshSync()
    const t = setInterval(() => { check(); refreshSync() }, 30_000)
    const on = () => { setOnline(navigator.onLine); if (navigator.onLine) check() }
    addEventListener('online', on)
    addEventListener('offline', on)
    return () => { alive = false; clearInterval(t); removeEventListener('online', on); removeEventListener('offline', on) }
  }, [refreshSync])

  const changed = useCallback(() => { setVersion((v) => v + 1); refreshSync() }, [refreshSync])

  // Keep the on-device catalog current while the Mac is reachable.
  useEffect(() => {
    if (!online || !localState.enabled) return
    let stop = false
    const tick = () => refreshLocal(() => enableOnDevice(() => {})).catch(() => {})
    tick()
    const t = setInterval(() => { if (!stop) tick() }, 60_000)
    return () => { stop = true; clearInterval(t) }
  }, [online, localState.enabled, version])
  // Can't reach the Mac but the catalog is on this device: search and browse from it.
  const haveLocal = localState.enabled || Object.keys(localState.devices ?? {}).length > 0
  const source: DataSource = haveLocal && (!online || localState.preferDevice) ? device : server
  useEffect(() => { setVersion((v) => v + 1) }, [source])
  return (
    <Ctx.Provider value={{ source, localState, health, online, authError, needsPairing, sync, refreshSync, version, changed }}>
      {children}
    </Ctx.Provider>
  )
}

export function useApp(): AppState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useApp outside AppProvider')
  return v
}
