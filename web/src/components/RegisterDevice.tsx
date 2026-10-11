// A phone or another computer opening the app over Tailscale names itself once and registers (§9.4).
import { useState } from 'react'
import { api } from '../api/client'
import { useApp } from '../lib/app'
import { getDevice, saveDevice } from '../lib/device'

export function RegisterDevice() {
  const { health, changed } = useApp()
  const [dev, setDev] = useState(getDevice)
  const [name, setName] = useState(dev.name)
  const [error, setError] = useState<string | null>(null)
  if (!health || health.via !== 'tailscale' || dev.registered) return null
  const register = async () => {
    try {
      await api('/api/devices/register', { method: 'POST', json: { device_id: dev.device_id, name: name.trim(), platform: dev.platform } })
      const next = { ...dev, name: name.trim(), registered: true }
      saveDevice(next)
      setDev(next)
      changed()
    } catch (e) { setError((e as Error).message) }
  }
  return (
    <div className="card install" role="dialog" aria-label="Name this device">
      <strong>Name this {dev.platform === 'ios' ? 'iPhone' : dev.platform === 'android' ? 'phone' : 'device'}</strong>
      <span className="muted small">Photos you upload from here are shown as coming from it.</span>
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) register() }}>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} aria-label="Device name" />
        <button className="btn primary" disabled={!name.trim()}>Save</button>
      </form>
      {error && <div className="error">{error}</div>}
    </div>
  )
}

/** The device id uploads should be credited to: the Mac itself for a browser on the Mac, else this device. */
export function uploaderId(via: string | undefined, macId: string | undefined): string {
  return via === 'tailscale' || !macId ? getDevice().device_id : macId
}
