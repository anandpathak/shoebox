// The on-phone ledger (LOCAL_APP_PLAN.md §13.4): what's on the camera roll and how far each item got.
import type { Asset, Changes, Entry, Ledger, Settings, Status, Access } from './types'

export const MAX_ATTEMPTS = 8

export function emptyLedger(): Ledger { return { token: null, entries: {} } }

function fromAsset(a: Asset): Entry {
  return { id: a.id, modified: a.modified, created: a.created, kind: a.kind, filename: a.filename, bytes: a.bytes,
           screenshot: a.screenshot, albums: a.albums, cloudOnly: a.cloudOnly, state: 'pending', attempts: 0 }
}

/** Fold the phone's changes in: new items wait to be hashed, edited ones are hashed again, deleted ones go. */
export function applyChanges(ledger: Ledger, ch: Changes): Ledger {
  const entries = { ...ledger.entries }
  const seen = new Set<string>()
  for (const a of ch.assets) {
    seen.add(a.id)
    const old = entries[a.id]
    if (!old) { entries[a.id] = fromAsset(a); continue }
    const meta = { created: a.created, filename: a.filename, bytes: a.bytes, screenshot: a.screenshot, albums: a.albums,
                   cloudOnly: a.cloudOnly, kind: a.kind }
    if (a.modified > old.modified && old.state !== 'uploading') {
      // Edited: hash again. If the bytes didn't change (e.g. only favourited) nothing is sent.
      entries[a.id] = { ...old, ...meta, modified: a.modified, state: 'pending', sha256: undefined, attempts: 0,
                        nextTry: undefined, reason: undefined,
                        doneSha256: old.state === 'done' ? old.sha256 ?? old.doneSha256 : old.doneSha256 }
    } else {
      entries[a.id] = { ...old, ...meta }
    }
  }
  for (const id of ch.deleted) delete entries[id]
  if (ch.full) for (const id of Object.keys(entries)) if (!seen.has(id)) delete entries[id]
  return { ...ledger, token: ch.token, entries }
}

/** Why this item isn't backed up under these settings, or null if it should be. */
export function skipReason(e: Entry, s: Settings): string | null {
  if (e.kind === 'video' && !s.videos) return 'Videos are off'
  if (e.screenshot && !s.screenshots) return 'Screenshots are off'
  if (s.from != null && e.created != null && e.created < s.from) return 'Before the backup start date'
  if (s.albums && !(e.albums ?? []).some((a) => s.albums!.includes(a))) return 'Not in the chosen albums'
  return null
}

/** Settings can change at any time, so skips are worked out again on every run. */
export function applySettings(ledger: Ledger, s: Settings): Ledger {
  const entries: Record<string, Entry> = {}
  for (const e of Object.values(ledger.entries)) {
    if (e.state === 'pending' || e.state === 'hashed' || e.state === 'skipped') {
      const why = skipReason(e, s)
      if (why) entries[e.id] = { ...e, state: 'skipped', reason: why }
      else if (e.state === 'skipped') entries[e.id] = { ...e, state: e.sha256 ? 'hashed' : 'pending', reason: undefined }
      else entries[e.id] = e
    } else entries[e.id] = e
  }
  return { ...ledger, entries }
}

/** Retry later: 1, 2, 4 … minutes, at most 6 hours; failed for good after MAX_ATTEMPTS (until "Retry failed"). */
export function retry(e: Entry, now: number, reason: string, back: 'pending' | 'hashed'): Entry {
  const attempts = e.attempts + 1
  if (attempts >= MAX_ATTEMPTS) return { ...e, attempts, state: 'failed', reason, nextTry: undefined }
  return { ...e, attempts, state: back, reason, nextTry: now + Math.min(60_000 * 2 ** (attempts - 1), 6 * 3600_000) }
}

export function retryFailed(ledger: Ledger): Ledger {
  const entries = { ...ledger.entries }
  for (const e of Object.values(entries)) {
    if (e.state === 'failed') entries[e.id] = { ...e, state: e.sha256 ? 'hashed' : 'pending', attempts: 0, nextTry: undefined, reason: undefined }
  }
  return { ...ledger, entries }
}

export function due(e: Entry, now: number): boolean { return e.nextTry == null || e.nextTry <= now }

export function status(ledger: Ledger, enabled: boolean, access: Access, waitingFor: Status['waitingFor']): Status {
  let total = 0, backedUp = 0, failed = 0, skipped = 0
  for (const e of Object.values(ledger.entries)) {
    if (e.state === 'skipped') { skipped++; continue }
    total++
    if (e.state === 'done') backedUp++
    else if (e.state === 'failed') failed++
  }
  const waiting = total - backedUp - failed
  return { enabled, access, total, backedUp, waiting, waitingFor: waiting ? waitingFor : null, failed, skipped,
           lastRun: ledger.lastRun, lastError: ledger.lastError }
}
