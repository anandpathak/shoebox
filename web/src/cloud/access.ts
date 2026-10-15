// The read-only S3 key the Mac hands out (GET /api/viewer/access). In the phone app it lives in the
// iPhone Keychain / Android Keystore (PhotoBackup plugin); in a plain browser (development and tests) it
// falls back to localStorage.
import { plugin } from '../backup/native'

export interface ViewerAccess {
  bucket: string; region: string; key_id: string; secret: string
  viewer: string; head: string
  endpoint?: string | null; path_style?: boolean
}

const KEY = 'shoebox.viewerAccess'

type Store = {
  setViewerAccess(o: { json: string }): Promise<void>
  getViewerAccess(): Promise<{ json: string | null }>
  clearViewerAccess(): Promise<void>
}

const native = () => plugin() as unknown as Store | null

export async function loadAccess(): Promise<ViewerAccess | null> {
  const p = native()
  if (p?.getViewerAccess) {
    const { json } = await p.getViewerAccess()
    return json ? JSON.parse(json) : null
  }
  try { const s = localStorage.getItem(KEY); return s ? JSON.parse(s) : null } catch { return null }
}

export async function saveAccess(a: ViewerAccess): Promise<void> {
  const p = native()
  if (p?.setViewerAccess) return p.setViewerAccess({ json: JSON.stringify(a) })
  localStorage.setItem(KEY, JSON.stringify(a))
}

export async function clearAccess(): Promise<void> {
  const p = native()
  if (p?.clearViewerAccess) return p.clearViewerAccess()
  localStorage.removeItem(KEY)
}
