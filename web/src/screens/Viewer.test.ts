import { expect, it } from 'vitest'
import { metadataNotes } from './Viewer'

const up = [{ device_id: 'P', name: 'Pixel', platform: 'android', source_ref: 'upload:P:1:x.jpg' }]
const apple = [{ device_id: 'M', name: 'Mac', platform: 'macos', source_ref: 'applephotos:U' }]

it('flags uploads that lost their location or date', () => {
  expect(metadataNotes({ sources: up, lat: null, taken_at_source: 'last_modified' })).toHaveLength(2)
  expect(metadataNotes({ sources: up, lat: 12.9, taken_at_source: 'file' })).toEqual([])
  expect(metadataNotes({ sources: apple, lat: null, taken_at_source: 'applephotos' })).toEqual([])
})
