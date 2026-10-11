import { describe, expect, it } from 'vitest'
import { ago, bytes, dateLabel, duration, groupByMonth, monthLabel } from './format'

describe('format', () => {
  it('groups consecutive items by month', () => {
    const g = groupByMonth([{ taken_at: '2025-08-15T10:00' }, { taken_at: '2025-08-01' }, { taken_at: null }])
    expect(g.map((x) => [x.key, x.items.length])).toEqual([['2025-08', 2], ['unknown', 1]])
    expect(monthLabel('2025-08')).toBe('August 2025')
  })
  it('formats dates, durations, sizes and ages', () => {
    expect(dateLabel('2025-08-15T11:52:05+05:30')).toBe('15 Aug 2025 · 11:52')
    expect(duration(78.4)).toBe('1:18')
    expect(bytes(447488)).toBe('437 KB')
    expect(bytes(2_200_000)).toBe('2.1 MB')
    expect(ago('2026-10-11T10:00:00Z', Date.parse('2026-10-11T13:00:00Z'))).toBe('3 h ago')
  })
})
