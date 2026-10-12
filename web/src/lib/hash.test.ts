import { expect, it } from 'vitest'
import { sha256File } from './hash'

it('matches the known SHA-256 of "abc", across slice boundaries too', async () => {
  expect(await sha256File(new Blob(['abc']))).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  const big = new Uint8Array(9 * 1024 * 1024).fill(7)
  const whole = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', big)), (b) => b.toString(16).padStart(2, '0')).join('')
  expect(await sha256File(new Blob([big]))).toBe(whole)
})
