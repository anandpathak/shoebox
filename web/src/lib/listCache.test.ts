import { expect, it } from 'vitest'
import { neighbours, rememberList } from './listCache'

it('finds neighbours in the remembered list only', () => {
  rememberList('?q=beach', ['a', 'b', 'c'])
  expect(neighbours('?q=beach', 'b')).toEqual({ prev: 'a', next: 'c' })
  expect(neighbours('?q=beach', 'a')).toEqual({ prev: null, next: 'b' })
  expect(neighbours('?q=other', 'b')).toEqual({ prev: null, next: null })
})
