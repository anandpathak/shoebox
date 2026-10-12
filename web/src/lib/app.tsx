// App-wide state: which data source is in use, who we are, and connection/sync status.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import type { Health, SyncStatus } from '../api/types'
import { LocalDataSource, ServerDataSource, type DataSource } from '../data/source'
import { enableOnDevice } from '../local/enable'
import { refreshLocal } from '../local/refresh'
import { getLocal, onLocal, type LocalState } from '../local/state'

interface AppState {
  source: DataSource
  localState: LocalState
  health: Health | null
  online: boolean
  authError: string | null
  sync: SyncStatus | null
  refreshSync: () => void
  version: number          // bumps after edits/syncs so screens reload
  changed: () => void
}

const Ctx = createContext<AppState | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const server = useMemo(() => new ServerDataSource(), [])
  const device = useMemo(() => new LocalDataSource(), [])
  const [localState, setLocalState] = useState(getLocal)
  useEffect(() => onLocal(setLocalState), [])
  const [health, setHealth] = useState<Health | null>(null)
  const [online, setOnline] = useState(true)
  const [authError, setAuthError] = useState<string | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [version, setVersion] = useState(0)

  const refreshSync = useCallback(() => {
    api<SyncStatus>('/api/sync/status').then(setSync).catch(() => {})
  }, [])

  useEffect(() => {
    let alive = true
    const check = () => api<Health>('/api/health')
      .then((h) => { if (alive) { setHealth(h); setOnline(true); setAuthError(null) } })
      .catch((e) => {
        if (!alive) return
        if (e.status === 401 || e.status === 403) setAuthError(e.message)
        else setOnline(false)
      })
    check()
    refreshSync()
    const t = setInterval(() => { check(); refreshSync() }, 30_000)
    const on = () => { setOnline(navigator.onLine); check() }
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
  const source: DataSource = !online && localState.enabled ? device : server
  useEffect(() => { setVersion((v) => v + 1) }, [source])
  return (
    <Ctx.Provider value={{ source, localState, health, online, authError, sync, refreshSync, version, changed }}>
      {children}
    </Ctx.Provider>
  )
}

export function useApp(): AppState {
  const v = useContext(Ctx)
  if (!v) throw new Error('useApp outside AppProvider')
  return v
}
