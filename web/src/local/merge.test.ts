import { expect, it } from 'vitest'
import type { MediaItem } from '../api/types'
import { decodeCursor, mergeResults } from './query'

const item = (id: string, taken: string, devices: string[]) => ({ photo_id: id, taken_at: taken, devices }) as unknown as MediaItem

it('merges catalogs: one entry per photo with all its devices, newest first, paged', () => {
  const r = mergeResults([
    { name: 'iphone', items: [item('A', '2025-08-02', ['IPHONE']), item('B', '2025-08-01', ['IPHONE'])] },
    { name: 'pixel', items: [item('C', '2025-08-03', ['PIXEL']), item('A', '2025-08-02', ['PIXEL'])] },
  ], 2)
  expect(r.items.map((i) => i.photo_id)).toEqual(['C', 'A'])
  expect(r.items[1].devices).toEqual(['IPHONE', 'PIXEL'])
  expect((r.items[1] as MediaItem & { catalogs: string[] }).catalogs).toEqual(['iphone', 'pixel'])
  expect(decodeCursor(r.next)).toEqual(['2025-08-02', 'A'])
})
