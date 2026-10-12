import { afterEach, expect, it, vi } from 'vitest'
import { downloadResumable } from './download'

afterEach(() => vi.unstubAllGlobals())

it('resumes with a Range request after the connection drops', async () => {
  const data = new Uint8Array(1000).map((_, i) => i % 251)
  const calls: (string | null)[] = []
  vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
    const range = (init.headers as Record<string, string>).Range ?? null
    calls.push(range)
    if (!range) {   // first attempt: 400 bytes, then the connection breaks
      let sent = false
      const body = new ReadableStream({ pull(c) { if (!sent) { sent = true; c.enqueue(data.slice(0, 400)) } else c.error(new Error('network lost')) } })
      return new Response(body, { status: 200, headers: { 'content-length': '1000', etag: '"v1"', 'x-ops-cursor': '42' } })
    }
    const from = Number(range.match(/bytes=(\d+)-/)![1])
    return new Response(data.slice(from), { status: 206, headers: { 'content-length': String(1000 - from), etag: '"v1"' } })
  })
  const got: number[] = []
  const res = await downloadResumable('/x', (n) => got.push(n), 3)
  expect(calls).toEqual([null, 'bytes=400-'])
  expect(Array.from(res.bytes)).toEqual(Array.from(data))
  expect(res.headers.get('x-ops-cursor')).toBe('42')
  expect(got.at(-1)).toBe(1000)
}, 10_000)
