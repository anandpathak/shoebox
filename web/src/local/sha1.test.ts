import { expect, it } from 'vitest'
import { sha1Hex } from './sha1'

// Expected values from Python's hashlib.sha1(s.encode()).hexdigest()
it('matches known SHA-1 values, including non-ASCII and multi-block input', () => {
  expect(sha1Hex('')).toBe('da39a3ee5e6b4b0d3255bfef95601890afd80709')
  expect(sha1Hex('abc')).toBe('a9993e364706816aba3e25717850c26c9cd0d89d')
  expect(sha1Hex('asha')).toBe('b8797b3406a9b97f68a019b3c5403272e0304237')
  expect(sha1Hex('café ü 漢字')).toBe('76ef5656146f273d3c3d8adceeafa6a1ee2466a9')
  expect(sha1Hex('x'.repeat(1000))).toBe('c3efa690fa3fdd2e2526853eed670538ea127638')
})
