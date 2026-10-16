// "Back up this phone" (LOCAL_APP_PLAN.md §13.6): only inside the iPhone/Android app, where the
// PhotoBackup plugin can read the camera roll.
import { useEffect, useState } from 'react'
import { ago } from '../lib/format'
import { loadSettings } from '../backup/store'
import { available, backupNow, currentStatus, retryFailedNow, subscribe, updateSettings } from '../backup/runner'
import { askForLocation, locationAllowed, nativePlatform, startPageUrl } from '../backup/native'
import type { Settings, Status } from '../backup/types'

const WAITING: Record<NonNullable<Status['waitingFor']>, string> = {
  wifi: 'waiting for Wi-Fi', charging: 'videos wait for charging', mac: 'waiting for your Mac',
}

export function statusLine(s: Status): string {
  if (!s.enabled) return 'Automatic backup is off.'
  if (s.total === 0 && !s.lastRun) return 'Looking at your photos…'
  let line = `Backed up ${s.backedUp.toLocaleString()} of ${s.total.toLocaleString()}`
  if (s.waiting) line += ` · ${s.waiting.toLocaleString()} ${s.waitingFor ? WAITING[s.waitingFor] : 'to go'}`
  return line
}

function dateInput(ms: number | null): string { return ms == null ? '' : new Date(ms).toISOString().slice(0, 10) }

export function PhoneBackup() {
  const [status, setStatus] = useState<Status | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [busy, setBusy] = useState(false)
  const [locationOk, setLocationOk] = useState(true)
  useEffect(() => {
    if (!available()) return
    locationAllowed().then(setLocationOk).catch(() => {})
    loadSettings().then(setSettings).catch(() => {})
    currentStatus().then(setStatus).catch(() => {})
    return subscribe(setStatus)
  }, [])
  if (!available() || !settings) return null

  const change = (c: Partial<Settings>) => {
    setSettings({ ...settings, ...c })
    updateSettings(c).then(setSettings).catch(() => {})
  }
  const now = async () => { setBusy(true); try { const s = await backupNow(); if (s) setStatus(s) } finally { setBusy(false) } }
  const ios = nativePlatform() === 'ios'
  const box = (key: keyof Settings, label: string, disabled = false) => (
    <label className="check"><input type="checkbox" checked={Boolean(settings[key])} disabled={disabled}
      onChange={(e) => change({ [key]: e.target.checked })} />{label}</label>
  )

  return (
    <section className="card" aria-label="Back up this phone">
      <h2>Back up this phone</h2>
      <p className="muted small">New photos and videos on this {ios ? 'iPhone' : 'phone'} are sent to your Mac and archived.
        Anything already in the archive (for example from the Mac's Photos library) isn't sent again.</p>
      <label className="check strong"><input type="checkbox" checked={settings.enabled}
        onChange={(e) => change({ enabled: e.target.checked })} />Back up automatically</label>
      {status && (
        <>
          <p className="small" role="status" data-testid="backup-status">{statusLine(status)}</p>
          {status.enabled && status.access === 'limited' && (
            <p className="warn small">Only the photos you allowed are backed up. To back up everything, allow full access in
              {ios ? ' Settings → Shoebox → Photos.' : ' Settings → Apps → Shoebox → Permissions.'}</p>)}
          {status.enabled && (status.access === 'denied' || status.access === 'not-determined') && (
            <p className="warn small">Photo access is off for Shoebox. Turn it on in the phone's Settings.</p>)}
          {status.lastError && <p className="error small">{status.lastError}</p>}
          {status.enabled && !locationOk && (
            <div className="warn small" role="alert" data-testid="location-off">
              Photos are being backed up <strong>without their location</strong>: Shoebox isn't allowed to read it.
              <button className="btn" onClick={() => askForLocation().then(setLocationOk)}>Allow location</button>
              <span className="muted"> If that does nothing: Settings → Apps → Shoebox → Permissions → Photos and videos →
                allow access to media location.</span>
            </div>)}
          {status.lastRun && <p className="muted small">Last checked {ago(new Date(status.lastRun).toISOString())}
            {status.skipped ? ` · ${status.skipped.toLocaleString()} left out by the settings below` : ''}</p>}
        </>
      )}
      {settings.enabled && (
        <>
          <div className="row">
            <button className="btn primary" disabled={busy} onClick={now}>{busy ? 'Backing up…' : 'Back up now'}</button>
            {status && status.failed > 0 && <button className="btn" onClick={() => retryFailedNow()}>Retry {status.failed} failed</button>}
          </div>
          <div className="checks">
            {box('wifiOnly', 'Only on Wi-Fi')}
            {box('videos', 'Include videos')}
            {box('videosWhileCharging', 'Videos only while charging', !settings.videos)}
            {box('screenshots', 'Include screenshots')}
            {ios && box('iCloudDownloads', 'Download originals kept in iCloud')}
          </div>
          <label className="field">Back up photos taken from
            <input type="date" value={dateInput(settings.from)}
              onChange={(e) => change({ from: e.target.value ? Date.parse(e.target.value) : null })} />
          </label>
        </>
      )}
      <div className="row">
        <button className="btn" onClick={() => { location.href = startPageUrl()! }}>Connect to a different Mac</button>
      </div>
    </section>
  )
}
