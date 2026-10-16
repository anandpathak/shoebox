// The app loads the web UI straight from the Mac, so it's the same app (and the same sign-in) as in the
// browser. Only this start page ships inside the app.
import { parse, KEY } from './address.js'

const $ = (id) => document.getElementById(id)
const form = $('f'), input = $('url'), err = $('err'), status = $('status'), scanBtn = $('scan')
const scanner = globalThis.Capacitor?.Plugins?.CapacitorBarcodeScanner
const QR_CODE = 0                                             // CapacitorBarcodeScannerTypeHint.QR_CODE
const saved = localStorage.getItem(KEY)
const changing = new URLSearchParams(location.search).has('change')

async function reachable(base) {
  // In the app, ask the native side: this page is https (Android) / capacitor:// (iPhone), and a web request
  // from it to the Mac's plain http address on the Wi-Fi is blocked as mixed content before it leaves the phone.
  const native = globalThis.Capacitor?.Plugins?.PhotoBackup
  if (native?.ping) {
    try { return (await native.ping({ url: `${base}/api/health` })).reachable === true } catch { return false }
  }
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), 6000)
  try {
    // This page isn't on the Mac's origin, so the answer is opaque (no CORS); any answer means it's reachable.
    // Sign-in and pairing are the web app's job once it's open.
    await fetch(`${base}/api/health`, { signal: ctl.signal, mode: 'no-cors', cache: 'no-store' })
    return true
  } catch { return false } finally { clearTimeout(t) }
}

function open(base, pair) {
  location.replace(`${base}/${pair ? `?pair=${encodeURIComponent(pair)}` : ''}#/library`)
}

async function connect(text) {
  err.textContent = ''
  const got = parse(text)
  if (!got) {
    err.textContent = "That isn't an address of your Mac. Use the one it printed: http://192.168.…:8765 on Wi-Fi, or https://….ts.net."
    return
  }
  status.textContent = `Connecting to ${got.base.replace(/^https?:\/\//, '')}…`
  if (!(await reachable(got.base))) {
    status.textContent = ''
    err.textContent = "Can't reach your Mac. Is shoebox running, and is this phone on the same Wi-Fi (or Tailscale on)?"
    return
  }
  localStorage.setItem(KEY, got.base)
  open(got.base, got.pair)
}

if (scanner) {
  scanBtn.hidden = false
  $('or').hidden = false
  scanBtn.addEventListener('click', async () => {
    err.textContent = ''
    try {
      const { ScanResult } = await scanner.scanBarcode({
        hint: QR_CODE, scanInstructions: 'Point at the QR code shoebox shows on the Mac', scanButton: false,
      })
      if (ScanResult) { input.value = ScanResult.replace(/\?pair=.*/, ''); await connect(ScanResult) }
    } catch (e) {
      const msg = String(e?.message ?? e)
      if (!/cancel/i.test(msg)) err.textContent = /permission|denied/i.test(msg)
        ? 'Camera access is off for Shoebox. Allow it in Settings, or type the address below.'
        : `Couldn't scan: ${msg}`
    }
  })
}

form.addEventListener('submit', (e) => { e.preventDefault(); void connect(input.value) })

// Set up from the Mac under Sync → Use without your Mac: browse the library from S3, read-only.
const store = globalThis.Capacitor?.Plugins?.PhotoBackup
const browseBtn = $('browse')
let canBrowse = false
store?.getViewerAccess?.().then(({ json }) => {
  canBrowse = !!json
  browseBtn.hidden = !canBrowse
}).catch(() => {})
browseBtn.addEventListener('click', () => location.assign('browse/index.html#/library'))

if (saved) {
  input.value = saved.replace(/^https?:\/\//, '')
  if (!changing) {
    status.textContent = `Connecting to ${input.value}…`
    reachable(saved).then((ok) => {
      if (ok) open(saved)
      else {
        status.textContent = ''
        err.textContent = canBrowse ? "Can't reach your Mac right now. You can still browse your library, read-only."
          : "Can't reach your Mac. Is shoebox running, and is this phone on the same Wi-Fi (or Tailscale on)?"
      }
    })
  }
}
