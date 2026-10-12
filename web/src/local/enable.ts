// Download the library catalog onto this device (Catalogs → Use on this device; also after a 410).
import { gunzip, local } from './client'
import { downloadResumable } from './download'
import { setLocal, type LocalState } from './state'

export async function enableOnDevice(onProgress: (got: number, total: number) => void): Promise<LocalState> {
  // Ask to keep the data, but never wait on it: some browsers sit on this request (e.g. behind a prompt).
  try { await Promise.race([navigator.storage?.persist?.(), new Promise((r) => setTimeout(r, 1000))]) } catch { /* best effort */ }
  const { bytes: gz, headers } = await downloadResumable('/api/catalogs/library', onProgress)
  const { items } = await local.call<{ items: number }>('importCatalog', 'library', await gunzip(gz.buffer as ArrayBuffer))
  return setLocal({ enabled: true, downloadedAt: new Date().toISOString(), refreshedAt: new Date().toISOString(),
                    cursor: Number(headers.get('x-ops-cursor') ?? 0), epoch: headers.get('x-catalog-epoch'), items })
}
