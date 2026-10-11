import { expect, it } from 'vitest'
import { detectPlatform, ulid } from './device'

it('makes sortable 26-char ids', () => {
  const a = ulid(1000), b = ulid(2000)
  expect(a).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/)
  expect(a.slice(0, 10) < b.slice(0, 10)).toBe(true)
})

it('detects platforms', () => {
  expect(detectPlatform('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', 5)).toBe('ios')
  expect(detectPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 5)).toBe('ios')   // iPadOS
  expect(detectPlatform('Mozilla/5.0 (Linux; Android 15; Pixel 8)', 5)).toBe('android')
  expect(detectPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 0)).toBe('web')
})
