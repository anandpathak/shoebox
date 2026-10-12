// Pairing a phone with the Mac on the same Wi-Fi (`photoctl serve --lan`). The token the Mac hands out is
// kept here too, because the native app's background uploads can't use the browser's cookie.
import { api } from '../api/client'

const KEY = 'photoctl.pairToken'

/** Only where pairing applies: not on the Mac itself, not over Tailscale. */
export function canPair(host = location.hostname): boolean {
  return !['localhost', '127.0.0.1', '::1'].includes(host) && !host.endsWith('.ts.net')
}

export function pairToken(): string | null {
  try { return localStorage.getItem(KEY) } catch { return null }
}

export async function pairWith(code: string, name: string): Promise<void> {
  const { token } = await api<{ token: string }>('/api/pair', { method: 'POST', json: { code, name } })
  try { localStorage.setItem(KEY, token) } catch { /* the cookie still works for the web app */ }
}

/** The code from a scanned QR link (?pair=CODE), removed from the address bar once read. */
export function codeFromUrl(): string | null {
  const u = new URL(location.href)
  const code = u.searchParams.get('pair')
  if (code) {
    u.searchParams.delete('pair')
    history.replaceState(null, '', u.pathname + u.search + u.hash)
  }
  return code
}

/** "abcd efgh" / "ABCD-EFGH" → "ABCDEFGH" */
export function cleanCode(text: string): string { return text.toUpperCase().replace(/[^A-Z0-9]/g, '') }
