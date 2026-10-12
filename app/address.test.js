import { describe, expect, it } from 'vitest'
import { normalise, parse } from './www/address.js'

describe('Mac address', () => {
  it('accepts tailnet addresses in the forms people paste', () => {
    expect(normalise('my-mac.tail1234.ts.net')).toBe('https://my-mac.tail1234.ts.net')
    expect(normalise(' https://my-mac.tail1234.ts.net/#/library ')).toBe('https://my-mac.tail1234.ts.net')
    expect(normalise('http://127.0.0.1:8765')).toBe('http://127.0.0.1:8765')
  })
  it('accepts the Mac on the home network over http, keeping a pairing code', () => {
    expect(normalise('192.168.1.11:8765')).toBe('http://192.168.1.11:8765')
    expect(normalise('my-mac.local:8765')).toBe('http://my-mac.local:8765')
    expect(normalise('http://10.0.0.5:8765')).toBe('http://10.0.0.5:8765')
    expect(normalise('172.20.1.2:8765')).toBe('http://172.20.1.2:8765')
    expect(parse('http://192.168.1.11:8765/?pair=ABCDEFGH')).toEqual({ base: 'http://192.168.1.11:8765', pair: 'ABCDEFGH' })
  })
  it('refuses anything outside the tailnet or the home network, and plain http to the tailnet', () => {
    expect(normalise('http://my-mac.tail1234.ts.net')).toBeNull()
    expect(normalise('https://example.com')).toBeNull()
    expect(normalise('http://8.8.8.8')).toBeNull()
    expect(normalise('172.32.0.1')).toBeNull()
    expect(normalise('https://ts.net.example.com')).toBeNull()
    expect(normalise('')).toBeNull()
  })
})
