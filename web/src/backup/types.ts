// Automatic camera-roll backup from the native app (LOCAL_APP_PLAN.md §13). The decisions live here, in
// TypeScript, so they're tested once for both phones; the iOS/Android plugins only list, hash and upload files.

/** One camera-roll item as the phone reports it. A Live Photo's video is its own asset (`<id>/live`). */
export interface Asset {
  id: string                        // PHAsset.localIdentifier / MediaStore _ID
  modified: number                  // ms; a newer value means the item was edited
  created: number | null            // ms, when it was taken
  kind: 'photo' | 'video'
  filename: string
  bytes: number | null
  screenshot?: boolean
  albums?: string[]
  cloudOnly?: boolean               // iOS: the original is in iCloud and has to be downloaded first
}

export interface Changes { assets: Asset[]; deleted: string[]; token: string; full?: boolean }

export type Access = 'full' | 'limited' | 'denied' | 'not-determined'

export interface UploadRequest {
  assetId: string; url: string; sha256: string
  headers: Record<string, string>
  wifiOnly: boolean                 // native layer sets allowsCellularAccess / WorkManager network constraint
}
export interface UploadState {
  assetId: string; state: 'queued' | 'running' | 'done' | 'failed'
  status?: number                   // HTTP status once finished
  body?: string                     // server reply (JSON) once finished
  error?: string
}

/** What the native plugin (`PhotoBackup`) provides. */
export interface Phone {
  access(): Promise<Access>
  requestAccess(): Promise<Access>
  /** Items added/edited/deleted since `token` (null = everything). */
  changes(token: string | null): Promise<Changes>
  /** Prepare the original (download from iCloud if allowed) and hash it as it streams. */
  hash(id: string, allowNetwork: boolean): Promise<{ sha256: string; bytes: number }>
  /** Hand an upload to the OS (background URLSession / WorkManager); it keeps going if the app is suspended. */
  upload(req: UploadRequest): Promise<void>
  uploads(): Promise<UploadState[]>
  forget(assetIds: string[]): Promise<void>       // drop finished uploads and their temporary copies
  conditions(): Promise<{ wifi: boolean; charging: boolean; online: boolean }>
}

export type EntryState = 'pending' | 'hashed' | 'uploading' | 'done' | 'skipped' | 'failed'

/** The on-phone ledger: one row per camera-roll item (§13.4). */
export interface Entry {
  id: string
  modified: number
  created: number | null
  kind: Asset['kind']
  filename: string
  bytes: number | null
  screenshot?: boolean
  albums?: string[]
  state: EntryState
  sha256?: string
  doneSha256?: string               // what was backed up last; an edit that doesn't change the bytes isn't sent again
  reason?: string                   // why skipped / failed / waiting
  attempts: number
  nextTry?: number                  // ms; retry back-off
  cloudOnly?: boolean
  how?: 'uploaded' | 'already'      // already = the archive had it (e.g. from the Mac's Apple Photos)
}

export interface Settings {
  enabled: boolean
  wifiOnly: boolean                 // default on
  videos: boolean                   // include videos
  videosWhileCharging: boolean      // default on: videos only while charging
  screenshots: boolean              // default off
  albums: string[] | null           // null = every album
  from: number | null               // ms; null = everything
  iCloudDownloads: boolean          // iOS: fetch iCloud-only originals (on Wi-Fi only when wifiOnly)
}

export const DEFAULT_SETTINGS: Settings = {
  enabled: false, wifiOnly: true, videos: true, videosWhileCharging: true, screenshots: false, albums: null, from: null,
  iCloudDownloads: true,
}

export interface Ledger { token: string | null; entries: Record<string, Entry>; lastRun?: number; lastError?: string }

export interface Status {
  enabled: boolean
  access: Access
  total: number                     // items that should be backed up
  backedUp: number
  waiting: number
  waitingFor: 'wifi' | 'charging' | 'mac' | null
  failed: number
  skipped: number
  lastRun?: number
  lastError?: string
}
