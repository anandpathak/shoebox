import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { BackupList, Device } from '../api/types'
import { InstallHint } from '../components/InstallHint'
import { Notifications } from '../components/Notifications'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { ago, bytes } from '../lib/format'
import { backupNow } from '../backup/runner'

interface Verify {
  checksum: string; converged: boolean; s3: boolean
  devices: { device_id: string; name: string; platform: string; last_seen: string | null; pending_ops: number; same: boolean; this_device: boolean }[]
}

const PLATFORM: Record<string, string> = { macos: 'Mac', ios: 'iPhone / iPad', android: 'Android', web: 'Browser' }

export function hlcTime(hlc: string | null): string | null {
  if (!hlc) return null
  const ms = Number(hlc.slice(0, 13))
  return Number.isFinite(ms) && ms > 0 ? new Date(ms).toISOString() : null
}

export function Sync() {
  const { sync, refreshSync, source, version, changed, health } = useApp()
  const [devices, setDevices] = useState<Device[]>([])
  const [verify, setVerify] = useState<Verify | null>(null)
  const [backups, setBackups] = useState<BackupList | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    source.devices().then(setDevices).catch(() => {})
    api<Verify>('/api/sync/verify').then(setVerify).catch(() => {})
    if (health?.s3) api<BackupList>('/api/db/backups').then(setBackups).catch(() => {})
  }, [source, version, health?.s3])

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name)
    setError(null)
    try { await fn(); changed() } catch (e) { setError((e as Error).message) } finally { setBusy(null); refreshSync() }
  }

  const s3 = health?.s3 ?? false
  return (
    <>
      <TopBar title="Sync & devices" />
      <div className="stack">
        {error && <div className="error">{error}</div>}
        <InstallHint always />
        <section className="card">
          <h2>{!s3 ? 'This Mac only' : sync?.pending_ops ? `${sync.pending_ops} changes waiting to sync` : 'Up to date'}</h2>
          {!s3 && <p className="muted">The server runs without S3, so changes stay on this Mac. Start it with a bucket to sync devices.</p>}
          {s3 && sync && (
            <p className="muted small">
              Last sync {ago(sync.last_ok)}{sync.running ? ' · syncing now…' : ''}
              {sync.last_result && ` · sent ${sync.last_result.pushed_ops}, received ${sync.last_result.applied_ops}`}
              {sync.interval > 0 && ` · every ${sync.interval} s`}
            </p>
          )}
          {sync?.error && <div className="error">Last sync failed: {sync.error}</div>}
          {s3 && <div className="row"><button className="btn primary" disabled={busy !== null}
                       onClick={() => { void backupNow(); act('sync', () => api('/api/sync', { method: 'POST' })) }}>
            {busy === 'sync' ? 'Syncing…' : 'Sync now'}</button></div>}
        </section>

        <section className="card">
          <h2>Devices</h2>
          {verify?.devices.length ? (
            <p className={verify.converged ? 'ok small' : 'warn small'}>
              {verify.converged ? `All ${verify.devices.length} devices have the same library.`
                : 'Not every device has caught up yet. They match once each one syncs.'}
            </p>
          ) : null}
          <div className="list">
            {devices.map((d) => {
              const reg = verify?.devices.find((v) => v.device_id === d.device_id)
              const seen = reg?.last_seen ?? hlcTime(d.last_seen_hlc)
              return (
                <div key={d.device_id}>
                  <div style={{ minWidth: 0 }}>
                    <strong>{d.name ?? d.device_id}</strong>{reg?.this_device || d.device_id === health?.device.device_id ? <span className="muted"> · this Mac</span> : null}
                    <div className="muted small">{PLATFORM[d.platform ?? ''] ?? d.platform} · {d.items} items from it · seen {ago(seen)}</div>
                  </div>
                  {reg && !reg.this_device && <span className={reg.same ? 'pill ok-pill' : 'pill'}>{reg.same ? 'in step' : 'behind'}</span>}
                </div>
              )
            })}
            {devices.length === 0 && <div className="muted">No devices yet.</div>}
          </div>
        </section>

        <Notifications />
        {s3 && (
          <section className="card">
            <h2>Database backups</h2>
            <p className="muted small">
              {backups?.last ? `Last backup ${ago(backups.last.created_at)}.` : 'No backup yet.'} Backups run after every
              archive and keep 30 daily and 12 monthly copies.
            </p>
            {backups && backups.backups.length > 0 && (
              <div className="list">
                {backups.backups.slice(0, 5).map((b) => (
                  <div key={b.prefix}><span>{new Date(b.created_at).toLocaleString()}</span><span className="muted small">{bytes(b.bytes)}</span></div>
                ))}
              </div>
            )}
            <div className="row"><button className="btn" disabled={busy !== null}
                 onClick={() => act('backup', async () => { await api('/api/db/backup', { method: 'POST' }); setBackups(await api<BackupList>('/api/db/backups')) })}>
              {busy === 'backup' ? 'Backing up…' : 'Back up databases now'}</button></div>
          </section>
        )}
      </div>
    </>
  )
}
