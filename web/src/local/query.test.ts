import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { buildFts, decodeCursor, encodeCursor, parseQuery } from './query'

const vectors = JSON.parse(readFileSync(resolve(__dirname, '../../../spec/query_vectors.json'), 'utf8'))

describe('query vectors (shared with Python)', () => {
  for (const c of vectors.cases) {
    it(c.text || '(empty)', () => {
      const parsed = parseQuery(c.text)
      expect(parsed).toEqual(c.expect)
      expect(buildFts(parsed.terms, { person: c.person, place: c.place, tag: c.tag, album: c.album })).toBe(c.fts)
    })
  }
  it('cursors round-trip, including non-ASCII', () => {
    expect(decodeCursor(encodeCursor({ taken_at: '2025-08-15T11:52:05+05:30', photo_id: 'ÄBC' }))).toEqual(['2025-08-15T11:52:05+05:30', 'ÄBC'])
  })
})
