import type { ReactNode } from 'react'
import { useApp } from '../lib/app'
import { Icon } from './Icon'

export const TABS = [
  { name: 'library', label: 'Library', icon: 'library' },
  { name: 'people', label: 'People', icon: 'people' },
  { name: 'uploads', label: 'Uploads', icon: 'upload' },
  { name: 'sync', label: 'Sync', icon: 'sync' },
] as const

export function StatusBar() {
  const { health, online, authError, sync } = useApp()
  if (authError) return <div className="status error"><i /><span>{authError}</span></div>
  if (!online) return <div className="status offline"><i /><span>Offline — can't reach your Mac</span></div>
  const via = health?.via === 'tailscale' ? ' · tailnet' : ''
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
