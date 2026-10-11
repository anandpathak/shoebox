import { expect, it } from 'vitest'
import { hlcTime } from './Sync'

it('reads the wall time out of an HLC', () => {
  expect(hlcTime('1791710525500-0000-MAC')).toBe('2026-10-11T09:22:05.500Z')
  expect(hlcTime(null)).toBeNull()
})
