// Turn on push notifications for this device ("Archive finished", "Original ready").
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { detectPlatform, getDevice } from '../lib/device'
import { isStandalone } from './InstallHint'

function b64ToBytes(b64: string): Uint8Array {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

export function Notifications() {
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
  const [state, setState] = useState<'off' | 'on' | 'denied' | 'busy'>('off')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!supported) return
    if (Notification.permission === 'denied') { setState('denied'); return }
    navigator.serviceWorker.ready.then((r) => r.pushManager.getSubscription()).then((s) => setState(s ? 'on' : 'off')).catch(() => {})
  }, [supported])

  const enable = async () => {
    setState('busy')
    setError(null)
    try {
      if ((await Notification.requestPermission()) !== 'granted') { setState('denied'); return }
      const reg = await navigator.serviceWorker.ready
      const { publicKey } = await api<{ publicKey: string }>('/api/push/key')
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(publicKey) as BufferSource })
      await api('/api/push/subscribe', { method: 'POST', json: { subscription: sub.toJSON(), device_id: getDevice().device_id } })
      setState('on')
    } catch (e) { setError((e as Error).message); setState('off') }
  }
  const disable = async () => {
    const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription()
    if (sub) { await api('/api/push/unsubscribe', { method: 'POST', json: { endpoint: sub.endpoint } }); await sub.unsubscribe() }
    setState('off')
  }

  const ios = detectPlatform() === 'ios'
  return (
    <section className="card">
      <h2>Notifications</h2>
      <p className="muted small">Get told when an archive run finishes or an original is ready to download.</p>
      {!supported || (ios && !isStandalone())
        ? <p className="muted small">{ios ? 'On iPhone, add Shoebox to your Home Screen first, then turn notifications on from there.'
            : 'This browser can’t show notifications.'}</p>
        : state === 'denied'
          ? <p className="muted small">Notifications are blocked for this site. Allow them in the browser’s site settings.</p>
          : <div className="row">
              {state === 'on'
                ? <><span className="pill ok-pill">On for this device</span>
                    <button className="btn" onClick={() => api('/api/push/test', { method: 'POST' })}>Send a test</button>
                    <button className="btn" onClick={disable}>Turn off</button></>
                : <button className="btn primary" disabled={state === 'busy'} onClick={enable}>Turn on notifications</button>}
            </div>}
      {error && <div className="error">{error}</div>}
    </section>
  )
}
