export const KEY = 'photoframing.mac'

/** "my-mac.tail1234.ts.net/" → "https://my-mac.tail1234.ts.net"; anything that isn't the tailnet (or this phone) → null. */
export function normalise(text) {
  let s = String(text || '').trim()
  if (!s) return null
  if (!/^[a-z]+:\/\//i.test(s)) s = `https://${s}`
  let u
  try { u = new URL(s) } catch { return null }
  const local = ['127.0.0.1', 'localhost', '10.0.2.2'].includes(u.hostname)
  if (!local && (u.protocol !== 'https:' || !u.hostname.endsWith('.ts.net'))) return null
  return `${u.protocol}//${u.host}`
}
