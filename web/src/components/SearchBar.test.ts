import { describe as d, expect, it } from 'vitest'
import { describe } from './SearchBar'

d('describe', () => {
  it('explains how a query was understood', () => {
    expect(describe({ terms: ['beach'], date_from: '2024-12-01', date_to: '2025-01-01', month: null, media_type: 'video' }))
      .toBe('December 2024 · videos · “beach”')
    expect(describe({ terms: [], date_from: '2023-01-01', date_to: '2024-01-01', month: null, media_type: null })).toBe('2023')
    expect(describe({ terms: ['diwali'], date_from: null, date_to: null, month: '11', media_type: null }))
      .toBe('November, any year · “diwali”')
  })
})
