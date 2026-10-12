// Synchronous SHA-1 of a UTF-8 string (person ids are 'p_' + sha1(name)[:16], same as the Python side).
export function sha1Hex(text: string): string {
  const msg = new TextEncoder().encode(text)
  const len = msg.length
  const words = new Uint32Array((((len + 8) >> 6) + 1) * 16)
  for (let i = 0; i < len; i++) words[i >> 2] |= msg[i] << (24 - (i % 4) * 8)
  words[len >> 2] |= 0x80 << (24 - (len % 4) * 8)
  words[words.length - 1] = len * 8
  let [a, b, c, d, e] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0]
  const w = new Uint32Array(80)
  for (let i = 0; i < words.length; i += 16) {
    for (let t = 0; t < 80; t++) {
      w[t] = t < 16 ? words[i + t] : rotl(w[t - 3] ^ w[t - 8] ^ w[t - 14] ^ w[t - 16], 1)
    }
    let [A, B, C, D, E] = [a, b, c, d, e]
    for (let t = 0; t < 80; t++) {
      const f = t < 20 ? (B & C) | (~B & D) : t < 40 ? B ^ C ^ D : t < 60 ? (B & C) | (B & D) | (C & D) : B ^ C ^ D
      const k = t < 20 ? 0x5a827999 : t < 40 ? 0x6ed9eba1 : t < 60 ? 0x8f1bbcdc : 0xca62c1d6
      const tmp = (rotl(A, 5) + f + E + k + w[t]) >>> 0
      E = D; D = C; C = rotl(B, 30); B = A; A = tmp
    }
    a = (a + A) >>> 0; b = (b + B) >>> 0; c = (c + C) >>> 0; d = (d + D) >>> 0; e = (e + E) >>> 0
  }
  return [a, b, c, d, e].map((x) => x.toString(16).padStart(8, '0')).join('')
}

function rotl(x: number, n: number) {
  return ((x << n) | (x >>> (32 - n))) >>> 0
}
