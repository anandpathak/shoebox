// The app loads the web UI straight from the Mac, so it's the same app (and the same Tailscale sign-in)
// as in the browser. Only this start page ships inside the app.
import { normalise, KEY } from './address.js'

const form = document.getElementById('f')
const input = document.getElementById('url')
const err = document.getElementById('err')
const saved = localStorage.getItem(KEY)
const retry = new URLSearchParams(location.search).has('change')

async function reachable(base) {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), 6000)
  try {
    const r = await fetch(`${base}/api/health`, { signal: ctl.signal, credentials: 'include' })
    return r.ok || r.status === 401 || r.status === 403   // the Mac answered; sign-in is the web app's business
  } catch { return false } finally { clearTimeout(t) }
}

function open(base) { location.replace(`${base}/#/library`) }

if (saved && !retry) {
  input.value = saved
  reachable(saved).then((ok) => ok ? open(saved) : (err.textContent = "Can't reach your Mac. Is Tailscale on?"))
}

form.addEventListener('submit', async (e) => {
  e.preventDefault()
  err.textContent = ''
  const base = normalise(input.value)
  if (!base) { err.textContent = 'Use the https://….ts.net address the Mac printed.'; return }
  if (!(await reachable(base))) { err.textContent = "Can't reach that address. Is Tailscale on for this phone?"; return }
  localStorage.setItem(KEY, base)
  open(base)
})
