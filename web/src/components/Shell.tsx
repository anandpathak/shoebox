import { useEffect, useState, type ReactNode } from 'react'
import { useApp } from '../lib/app'
import { ago } from '../lib/format'
import { local } from '../local/client'
import { Icon } from './Icon'

export const TABS = [
  { name: 'library', label: 'Library', icon: 'library' },
  { name: 'people', label: 'People', icon: 'people' },
  { name: 'uploads', label: 'Uploads', icon: 'upload' },
  { name: 'catalogs', label: 'Catalogs', icon: 'catalogs' },
  { name: 'sync', label: 'Sync', icon: 'sync' },
] as const

function usePendingOnDevice(enabled: boolean, version: number): number {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!enabled) { setN(0); return }
    local.call<number>('outboxCount', 'library').then(setN).catch(() => setN(0))
  }, [enabled, version])
  return n
}

export function StatusBar() {
  const { health, online, authError, sync, localState, version } = useApp()
  const pendingHere = usePendingOnDevice(localState.enabled, version)
  if (authError) return <div className="status error"><i /><span>{authError}</span></div>
  if (!online) {
    return (
      <div className="status offline" role="status"><i /><span>{localState.enabled
        ? `Offline — on-device catalog from ${ago(localState.refreshedAt ?? localState.downloadedAt)}`
        : Object.keys(localState.devices ?? {}).length ? 'Offline — searching downloaded catalogs'
        : "Offline — can't reach your Mac"}</span>
        {pendingHere > 0 && <span className="pill">{pendingHere} {pendingHere === 1 ? 'edit' : 'edits'} waiting</span>}</div>
    )
  }
  const via = health?.via === 'tailscale' ? ' · tailnet' : ''
  if (localState.preferDevice) {
    return <div className="status" role="status"><i /><span>Searching catalogs on this device</span></div>
  }
  return (
    <div className="status" role="status">
      <i /><span>{health ? `Online via ${health.device.name}${via}` : 'Connecting…'}</span>
      {sync && sync.pending_ops > 0 && <span className="pill">{sync.pending_ops} unsent</span>}
    </div>
  )
}

export function Shell({ current, children }: { current: string; children: ReactNode }) {
  return (
    <div className="shell">
      <nav className="tabbar" aria-label="Sections">
        <div className="brand"><img src="/favicon.svg" alt="" />Photoframing</div>
        {TABS.map((t) => (
          <a key={t.name} href={`#/${t.name}`} aria-current={current === t.name ? 'page' : undefined}>
            <Icon name={t.icon} />{t.label}
          </a>
        ))}
      </nav>
      <div className="main">{children}</div>
    </div>
  )
}

export function TopBar({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="topbar">
      <div className="topbar-row"><h1>{title}</h1><StatusBar /></div>
      {children}
    </header>
  )
}
