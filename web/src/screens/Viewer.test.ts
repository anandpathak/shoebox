import { expect, it } from 'vitest'
import { coords, metadataNotes } from './Viewer'

const up = [{ device_id: 'P', name: 'Pixel', platform: 'android', source_ref: 'upload:P:1:x.jpg' }]
const apple = [{ device_id: 'M', name: 'Mac', platform: 'macos', source_ref: 'applephotos:U' }]

it('flags uploads that lost their location or date', () => {
  expect(metadataNotes({ sources: up, lat: null, taken_at_source: 'last_modified' })).toHaveLength(2)
  expect(metadataNotes({ sources: up, lat: 12.9, taken_at_source: 'file' })).toEqual([])
  expect(metadataNotes({ sources: apple, lat: null, taken_at_source: 'applephotos' })).toEqual([])
})

it('the "no location" advice fits where the photo came from', () => {
  const src = (ref: string, platform: string) => [{ device_id: 'd', name: null, platform, source_ref: ref }]
  const note = (ref: string, platform: string) => metadataNotes({ sources: src(ref, platform), lat: null, taken_at_source: 'file' })[0]
  expect(note('upload:d:1:x.jpg', 'android')).toMatch(/Android removes it .* Back up this phone/)
  expect(note('upload:d:1:x.jpg', 'ios')).toMatch(/Options → Location/)
  expect(note('android:123', 'android')).toMatch(/location in photos/)
  expect(note('ios:ABC', 'ios')).toMatch(/camera had location off/)
})

it('coordinates read like a map', () => {
  expect(coords(14.5339, 74.3169)).toBe('14.5339° N, 74.3169° E')
  expect(coords(-33.8568, -151.2153)).toBe('33.8568° S, 151.2153° W')
})
