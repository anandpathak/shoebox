export const KEY = 'shoebox.mac'

const PRIVATE = [/^10\./, /^192\.168\./, /^172\.(1[6-9]|2\d|3[01])\./, /^127\./]

function local(host) {
  return host === 'localhost' || host.endsWith('.local') || PRIVATE.some((r) => r.test(host))
}

/**
 * "my-mac.tail1234.ts.net/" → "https://my-mac.tail1234.ts.net"; "192.168.1.11:8765" → "http://192.168.1.11:8765".
 * The pairing code from a scanned link is kept: "http://192.168.1.11:8765/?pair=ABCD" → { base, pair: "ABCD" }.
 * Anything that isn't the tailnet or this home network → null.
 */
export function parse(text) {
  let s = String(text || '').trim()
  if (!s) return null
  const host = s.replace(/^[a-z]+:\/\//i, '').split(/[/:?#]/)[0].toLowerCase()
  if (!/^[a-z]+:\/\//i.test(s)) s = `${local(host) ? 'http' : 'https'}://${s}`
  let u
  try { u = new URL(s) } catch { return null }
  const h = u.hostname.toLowerCase()
  const ok = (u.protocol === 'https:' && h.endsWith('.ts.net')) || (local(h) && (u.protocol === 'http:' || u.protocol === 'https:'))
  if (!ok) return null
  return { base: `${u.protocol}//${u.host}`, pair: u.searchParams.get('pair') }
}

export function normalise(text) { return parse(text)?.base ?? null }
