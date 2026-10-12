// Download a (gzipped) catalog with progress, resuming with HTTP Range requests if the connection drops.
export interface Downloaded { bytes: Uint8Array; headers: Headers }

export async function downloadResumable(url: string, onProgress?: (got: number, total: number) => void,
                                        attempts = 6): Promise<Downloaded> {
  const chunks: Uint8Array[] = []
  let got = 0
  let total = 0
  let headers: Headers | null = null
  let etag: string | null = null
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const h: Record<string, string> = got ? { Range: `bytes=${got}-` } : {}
      if (got && etag) h['If-Range'] = etag
      const res = await fetch(url, { headers: h, credentials: 'same-origin' })
      if (!res.ok) throw new Error(`Download failed (${res.status})`)
      if (got && res.status !== 206) { chunks.length = 0; got = 0 }      // the file changed: start again
      if (!headers) headers = res.headers
      etag = res.headers.get('etag') ?? etag
      if (!total) total = Number(res.headers.get('content-length') ?? 0) + got
      const reader = res.body!.getReader()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        chunks.push(value)
        got += value.length
        onProgress?.(got, total)
      }
      const out = new Uint8Array(got)
      let pos = 0
      for (const c of chunks) { out.set(c, pos); pos += c.length }
      return { bytes: out, headers: headers! }
    } catch (e) {
      if (attempt === attempts - 1) throw e
      await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)))
    }
  }
  throw new Error('Download failed')
}
