import { expect, it } from 'vitest'
import { runSummary } from './Uploads'

it('says when nothing was new instead of "0 of 0 done"', () => {
  expect(runSummary({ todo: 0, processed: 0, matched: 1247, skipped: 1247, status: 'done' })).toBe('Nothing new: all 1,247 items here are already archived.')
  expect(runSummary({ todo: 0, processed: 0, matched: 0, skipped: 0, status: 'done' })).toBe('Nothing matched.')
  expect(runSummary({ todo: 41, processed: 12, matched: 1288, skipped: 1247, status: 'running' }, 1))
    .toBe('12 of 41 new items done · 1 failed · 1,247 already archived before')
  expect(runSummary({ todo: null, processed: 0, status: 'running' })).toBe('Reading the Photos library…')
})
