import { describe, expect, it } from 'vitest'
import { normalise } from './www/address.js'

describe('Mac address', () => {
  it('accepts tailnet addresses in the forms people paste', () => {
    expect(normalise('my-mac.tail1234.ts.net')).toBe('https://my-mac.tail1234.ts.net')
    expect(normalise(' https://my-mac.tail1234.ts.net/#/library ')).toBe('https://my-mac.tail1234.ts.net')
    expect(normalise('http://127.0.0.1:8765')).toBe('http://127.0.0.1:8765')
  })
  it('refuses anything outside the tailnet, and plain http', () => {
    expect(normalise('http://my-mac.tail1234.ts.net')).toBeNull()
    expect(normalise('https://example.com')).toBeNull()
    expect(normalise('https://ts.net.example.com')).toBeNull()
    expect(normalise('')).toBeNull()
  })
})
