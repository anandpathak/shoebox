import { describe, expect, it } from 'vitest'
import { canPair, cleanCode } from './pairing'

describe('pairing', () => {
  it('only applies away from the Mac and outside the tailnet', () => {
    expect(canPair('192.168.1.11')).toBe(true)
    expect(canPair('my-mac.local')).toBe(true)
    expect(canPair('127.0.0.1')).toBe(false)
    expect(canPair('localhost')).toBe(false)
    expect(canPair('my-mac.tail1234.ts.net')).toBe(false)
  })
  it('accepts codes typed loosely', () => {
    expect(cleanCode('abcd-ef gh')).toBe('ABCDEFGH')
  })
})
