import { describe, expect, it } from 'vitest'
import { hex, hmac, presign, sha256, uriEncode } from './sigv4'

describe('SHA-256 / HMAC', () => {
  it('matches WebCrypto', async () => {
    for (const s of ['', 'abc', 'x'.repeat(55), 'x'.repeat(56), 'y'.repeat(64), 'z'.repeat(1000)]) {
      const want = hex(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))))
      expect(hex(sha256(new TextEncoder().encode(s)))).toBe(want)
    }
    const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('key'), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
    const want = hex(new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode('The quick brown fox'))))
    expect(hex(hmac('key', 'The quick brown fox'))).toBe(want)
  })
})

describe('presigned S3 URLs', () => {
  it("reproduces AWS's documented example", () => {
    // docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html (AWS's public example key)
    const url = presign({ keyId: 'AKIA' + 'IOSFODNN7EXAMPLE', secret: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', region: 'us-east-1' },
      { bucket: 'examplebucket', key: 'test.txt', host: 'examplebucket.s3.amazonaws.com' },
      { when: new Date('2013-05-24T00:00:00Z'), expires: 86400 })
    expect(url).toContain('X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404')
  })

  it('encodes keys, supports path-style endpoints, and is stable for an hour', () => {
    const c = { keyId: 'K', secret: 'S', region: 'ap-south-1' }
    const a = presign(c, { bucket: 'b', key: 'thumb/2024/2024-01/A B.jpg' })
    expect(a.startsWith('https://b.s3.ap-south-1.amazonaws.com/thumb/2024/2024-01/A%20B.jpg?')).toBe(true)
    expect(presign(c, { bucket: 'b', key: 'thumb/2024/2024-01/A B.jpg' })).toBe(a)
    const local = presign(c, { bucket: 'e2e', key: 'viewer/x', endpoint: 'http://127.0.0.1:5000', pathStyle: true })
    expect(local.startsWith('http://127.0.0.1:5000/e2e/viewer/x?')).toBe(true)
    expect(uriEncode('a/b c', true)).toBe('a/b%20c')
  })
})
