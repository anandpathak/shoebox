import { describe, expect, it } from 'vitest'
import { parse } from './router'

describe('parse', () => {
  it('defaults to the library', () => expect(parse('').name).toBe('library'))
  it('reads params and query', () => {
    const r = parse('#/media/ABC%20D?from=search')
    expect(r.name).toBe('media')
    expect(r.params).toEqual(['ABC D'])
    expect(r.query.get('from')).toBe('search')
  })
})
