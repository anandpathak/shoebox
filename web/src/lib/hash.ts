// SHA-256 of a file, streamed in 4 MB slices so large videos never sit in memory at once.
import { createSHA256 } from 'hash-wasm'

export async function sha256File(file: Blob, onProgress?: (done: number) => void): Promise<string> {
  const h = await createSHA256()
  h.init()
  const step = 4 * 1024 * 1024
  for (let pos = 0; pos < file.size; pos += step) {
    h.update(new Uint8Array(await file.slice(pos, pos + step).arrayBuffer()))
    onProgress?.(Math.min(pos + step, file.size))
  }
  return h.digest('hex')
}
