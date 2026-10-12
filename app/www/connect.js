// The app loads the web UI straight from the Mac, so it's the same app (and the same Tailscale sign-in)
// as in the browser. Only this start page ships inside the app.
import { parse, KEY } from './address.js'

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

function open(base, pair) { location.replace(`${base}/${pair ? `?pair=${encodeURIComponent(pair)}` : ''}#/library`) }

if (saved && !retry) {
  input.value = saved
  reachable(saved).then((ok) => ok ? open(saved) : (err.textContent = "Can't reach your Mac. Is Tailscale on?"))
}

form.addEventListener('submit', async (e) => {
  e.preventDefault()
  err.textContent = ''
  const got = parse(input.value)
  if (!got) { err.textContent = 'Use the address the Mac printed (https://….ts.net, or http://192.168.… on your Wi-Fi).'; return }
  if (!(await reachable(got.base))) { err.textContent = "Can't reach that address. Is the phone on the same Wi-Fi (or Tailscale on)?"; return }
  localStorage.setItem(KEY, got.base)
  open(got.base, got.pair)
})
