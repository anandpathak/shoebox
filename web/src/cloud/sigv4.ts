// AWS Signature V4 presigned GET URLs for S3, computed synchronously on the phone (an <img src> needs a URL
// right away; WebCrypto is async). SHA-256/HMAC in plain TypeScript, checked against AWS's published example
// and against WebCrypto in sigv4.test.ts.

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
  0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
  0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
])

export function sha256(data: Uint8Array): Uint8Array {
  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19])
  const bitLen = data.length * 8
  const padded = new Uint8Array(((data.length + 9 + 63) >> 6) << 6)
  padded.set(data)
  padded[data.length] = 0x80
  const view = new DataView(padded.buffer)
  view.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000))
  view.setUint32(padded.length - 4, bitLen >>> 0)
  const w = new Uint32Array(64)
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4)
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15], b = w[i - 2]
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3)
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0
    }
    let [a, b, c, d, e, f, g, hh] = h
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))
      const t1 = (hh + S1 + ((e & f) ^ (~e & g)) + K[i] + w[i]) >>> 0
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))
      const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) >>> 0
      hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh
  }
  const out = new Uint8Array(32)
  const ov = new DataView(out.buffer)
  for (let i = 0; i < 8; i++) ov.setUint32(i * 4, h[i])
  return out
}

const enc = new TextEncoder()
const bytes = (s: string | Uint8Array) => (typeof s === 'string' ? enc.encode(s) : s)
export const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, '0')).join('')

export function hmac(key: string | Uint8Array, msg: string | Uint8Array): Uint8Array {
  let k = bytes(key)
  if (k.length > 64) k = sha256(k)
  const block = new Uint8Array(64)
  block.set(k)
  const inner = new Uint8Array(64 + bytes(msg).length)
  const outer = new Uint8Array(64 + 32)
  for (let i = 0; i < 64; i++) { inner[i] = block[i] ^ 0x36; outer[i] = block[i] ^ 0x5c }
  inner.set(bytes(msg), 64)
  outer.set(sha256(inner), 64)
  return sha256(outer)
}

/** RFC 3986 encoding as S3 wants it (keeps / in object keys when asked). */
export function uriEncode(s: string, keepSlash = false): string {
  return [...enc.encode(s)].map((b) => {
    const ch = String.fromCharCode(b)
    if (/[A-Za-z0-9\-._~]/.test(ch) || (keepSlash && ch === '/')) return ch
    return '%' + b.toString(16).toUpperCase().padStart(2, '0')
  }).join('')
}

export interface Credentials { keyId: string; secret: string; region: string }
export interface Target { bucket: string; key: string; endpoint?: string | null; pathStyle?: boolean; host?: string }

const signingKeys = new Map<string, Uint8Array>()

function signingKey(c: Credentials, day: string): Uint8Array {
  const id = `${c.keyId}/${day}/${c.region}`
  let k = signingKeys.get(id)
  if (!k) {
    k = hmac(hmac(hmac(hmac(`AWS4${c.secret}`, day), c.region), 's3'), 'aws4_request')
    signingKeys.set(id, k)
  }
  return k
}

/** A presigned GET URL. `when` defaults to the start of the current hour, so the same picture gets the
 * same URL for an hour and the browser can cache it. */
export function presign(c: Credentials, t: Target, opts: { when?: Date; expires?: number; query?: Record<string, string> } = {}): string {
  const when = opts.when ?? new Date(Math.floor(Date.now() / 3_600_000) * 3_600_000)
  const amzDate = when.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
  const day = amzDate.slice(0, 8)
  let origin: string, path: string
  if (t.endpoint) {
    origin = t.endpoint.replace(/\/$/, '')
    path = t.pathStyle ? `/${t.bucket}/${uriEncode(t.key, true)}` : `/${uriEncode(t.key, true)}`
  } else {
    origin = `https://${t.host ?? `${t.bucket}.s3.${c.region}.amazonaws.com`}`
    path = `/${uriEncode(t.key, true)}`
  }
  const host = new URL(origin).host
  const scope = `${day}/${c.region}/s3/aws4_request`
  const params: Record<string, string> = {
    'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
    'X-Amz-Credential': `${c.keyId}/${scope}`,
    'X-Amz-Date': amzDate,
    'X-Amz-Expires': String(opts.expires ?? 7200),
    'X-Amz-SignedHeaders': 'host',
    ...opts.query,
  }
  const query = Object.keys(params).sort().map((k) => `${uriEncode(k)}=${uriEncode(params[k])}`).join('&')
  const canonical = ['GET', path, query, `host:${host}\n`, 'host', 'UNSIGNED-PAYLOAD'].join('\n')
  const toSign = ['AWS4-HMAC-SHA256', amzDate, scope, hex(sha256(enc.encode(canonical)))].join('\n')
  const signature = hex(hmac(signingKey(c, day), toSign))
  return `${origin}${path}?${query}&X-Amz-Signature=${signature}`
}
