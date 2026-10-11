// "Install the app" help: Chrome/Android offer a prompt we can trigger; iOS needs Share → Add to Home Screen.
import { useEffect, useState } from 'react'
import { detectPlatform } from '../lib/device'

interface BeforeInstallPromptEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

const KEY = 'photoctl.installHintDismissed'

export function isStandalone(): boolean {
  return matchMedia('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true
}

export function InstallHint({ always = false }: { always?: boolean }) {
  const [prompt, setPrompt] = useState<BeforeInstallPromptEvent | null>(null)
  const [hidden, setHidden] = useState(() => {
    try { return !always && localStorage.getItem(KEY) === '1' } catch { return false }
  })
  useEffect(() => {
    const on = (e: Event) => { e.preventDefault(); setPrompt(e as BeforeInstallPromptEvent) }
    addEventListener('beforeinstallprompt', on)
    return () => removeEventListener('beforeinstallprompt', on)
  }, [])
  if (hidden || isStandalone()) return null
  const platform = detectPlatform()
  if (platform !== 'ios' && !prompt) return null
  const dismiss = () => { setHidden(true); try { localStorage.setItem(KEY, '1') } catch { /* ignore */ } }
  return (
    <div className="install card" role="note">
      <strong>Install Photoframing on this {platform === 'ios' ? 'iPhone' : 'device'}</strong>
      {platform === 'ios'
        ? <span className="muted small">Tap <b>Share</b> in Safari, then <b>Add to Home Screen</b>. Installed, it opens full screen
            and keeps its offline library (Safari clears data for sites that aren't installed after 7 days unused).</span>
        : <span className="muted small">Adds an app icon, opens full screen and keeps working offline.</span>}
      <div className="row">
        {prompt && <button className="btn primary" onClick={async () => { await prompt.prompt(); setPrompt(null) }}>Install app</button>}
        {!always && <button className="btn" onClick={dismiss}>Not now</button>}
      </div>
    </div>
  )
}
