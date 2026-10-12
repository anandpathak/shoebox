// One backup pass (LOCAL_APP_PLAN.md §13.2–13.4): collect finished uploads → read camera-roll changes →
// hash new/edited items → ask the Mac which ones it lacks → hand those to the OS to upload.
import { applyChanges, applySettings, due, retry, status } from './ledger'
import type { Entry, Ledger, Phone, Settings, Status, UploadState } from './types'

export interface Context {
  phone: Phone
  load(): Promise<Ledger>
  save(l: Ledger): Promise<void>
  /** Talks to the Mac (same origin as the web app). */
  fetch: typeof fetch
  origin: string
  deviceId: string
  platform: 'ios' | 'android'
  settings: Settings
  now?: () => number
  hashPerRun?: number
  inFlight?: number               // uploads handed to the OS at once
  onProgress?: (s: Status) => void
  auth?: string | null          // paired over Wi-Fi: background uploads can't use the cookie
}

const CHECK_BATCH = 500

export async function runBackup(ctx: Context): Promise<Status> {
  const now = ctx.now ?? Date.now
  const { phone, settings } = ctx
  let ledger = await ctx.load()
  const access = await phone.access()
  let waitingFor: Status['waitingFor'] = null
  const finish = async (error?: string) => {
    ledger = { ...ledger, lastRun: now(), lastError: error }
    await ctx.save(ledger)
    const s = status(ledger, settings.enabled, access, waitingFor)
    ctx.onProgress?.(s)
    return s
  }
  if (!settings.enabled) return status(ledger, false, access, null)
  if (access === 'denied' || access === 'not-determined') return finish('Photo access is off')

  ledger = await collectUploads(ctx, ledger, now())
  ledger = applySettings(applyChanges(ledger, await phone.changes(ledger.token)), settings)
  await ctx.save(ledger)

  const c = await phone.conditions()
  if (!c.online) { waitingFor = 'mac'; return finish() }
  const onWifi = c.wifi || !settings.wifiOnly

  // 1. hash
  const allowNetwork = settings.iCloudDownloads && onWifi
  let budget = ctx.hashPerRun ?? 200
  for (const e of Object.values(ledger.entries)) {
    if (budget <= 0) break
    if (e.state !== 'pending' || !due(e, now())) continue
    if (e.cloudOnly && !allowNetwork) {
      if (settings.iCloudDownloads) waitingFor = 'wifi'
      else ledger.entries[e.id] = { ...e, reason: 'Only in iCloud (iCloud downloads are off)' }
      continue
    }
    budget--
    let next: Entry
    try {
      const { sha256, bytes } = await phone.hash(e.id, allowNetwork)
      next = sha256 === e.doneSha256
        ? { ...e, sha256, bytes, state: 'done', reason: undefined }          // edited, but the bytes are the same
        : { ...e, sha256, bytes, state: 'hashed', reason: undefined }
    } catch (err) {
      next = retry(e, now(), `Couldn't read the original: ${message(err)}`, 'pending')
    }
    ledger = { ...ledger, entries: { ...ledger.entries, [e.id]: next } }
  }
  await ctx.save(ledger)

  // 2. which ones does the Mac lack?
  const hashed = Object.values(ledger.entries).filter((e) => e.state === 'hashed' && due(e, now()))
  const missing = new Set<string>()
  try {
    for (let i = 0; i < hashed.length; i += CHECK_BATCH) {
      const batch = hashed.slice(i, i + CHECK_BATCH)
      const r = await ctx.fetch(`${ctx.origin}/api/backup/check`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, credentials: 'include',
        body: JSON.stringify({ hashes: [...new Set(batch.map((e) => e.sha256!))] }),
      })
      if (!r.ok) throw new Error(`the Mac answered ${r.status}`)
      const res = await r.json() as { missing: string[]; archived: string[]; in_progress: string[] }
      const archived = new Set(res.archived), inProgress = new Set(res.in_progress)
      res.missing.forEach((h) => missing.add(h))
      const entries = { ...ledger.entries }
      for (const e of batch) {
        // in_progress: the Mac has received the file and is archiving it (failures stay in its inbox for retry)
        if (archived.has(e.sha256!) || inProgress.has(e.sha256!)) {
          entries[e.id] = { ...e, state: 'done', how: e.how ?? 'already', doneSha256: e.sha256, reason: undefined }
        }
      }
      ledger = { ...ledger, entries }
    }
  } catch (err) {
    waitingFor = 'mac'
    return finish(`Can't reach your Mac: ${message(err)}`)
  }

  // 3. upload what's missing
  const uploading = Object.values(ledger.entries).filter((e) => e.state === 'uploading').length
  let slots = (ctx.inFlight ?? 20) - uploading
  const sent = new Set<string>()      // two items with the same bytes: send one
  for (const e of Object.values(ledger.entries)) {
    if (e.state !== 'hashed' || !missing.has(e.sha256!) || sent.has(e.sha256!)) continue
    if (!onWifi) { waitingFor = 'wifi'; continue }
    if (e.kind === 'video' && settings.videosWhileCharging && !c.charging) { waitingFor ??= 'charging'; continue }
    if (slots <= 0) break
    try {
      await phone.upload({
        assetId: e.id, url: `${ctx.origin}/api/uploads/raw`, sha256: e.sha256!, wifiOnly: settings.wifiOnly,
        headers: { ...(ctx.auth ? { Authorization: `Bearer ${ctx.auth}` } : {}), 'X-Device-Id': ctx.deviceId, 'X-Asset-Id': e.id, 'X-Sha256': e.sha256!, 'X-Filename': e.filename,
                   ...(e.created ? { 'X-Last-Modified': String(e.created) } : {}) },
      })
      slots--
      sent.add(e.sha256!)
      ledger = { ...ledger, entries: { ...ledger.entries, [e.id]: { ...e, state: 'uploading', reason: undefined } } }
    } catch (err) {
      ledger = { ...ledger, entries: { ...ledger.entries, [e.id]: retry(e, now(), message(err), 'hashed') } }
    }
  }
  if (!waitingFor && Object.values(ledger.entries).some((e) => e.state === 'uploading')) waitingFor = 'mac'
  return finish()
}

/** Results of uploads the OS finished (possibly while the app was suspended). */
export async function collectUploads(ctx: Context, ledger: Ledger, now: number): Promise<Ledger> {
  const states = new Map<string, UploadState>((await ctx.phone.uploads()).map((u) => [u.assetId, u]))
  const entries = { ...ledger.entries }
  const finished: string[] = []
  for (const e of Object.values(entries)) {
    if (e.state !== 'uploading') continue
    const u = states.get(e.id)
    if (!u) { entries[e.id] = { ...e, state: 'hashed' }; continue }        // lost (e.g. app reinstalled): check again
    if (u.state === 'queued' || u.state === 'running') continue
    finished.push(e.id)
    if (u.state === 'done' && u.status === 200) {
      let reply: { status?: string } = {}
      try { reply = JSON.parse(u.body ?? '{}') } catch { /* keep {} */ }
      entries[e.id] = { ...e, state: 'done', how: reply.status === 'already_archived' ? 'already' : 'uploaded',
                        doneSha256: e.sha256, attempts: 0, reason: undefined, nextTry: undefined }
    } else if (u.status === 422) {
      entries[e.id] = retry({ ...e, sha256: undefined }, now, "The file changed while it was sent; it'll be read again", 'pending')
    } else if (u.status === 404) {
      entries[e.id] = { ...e, state: 'failed', reason: 'This phone isn\'t registered with the Mac yet' }
    } else {
      entries[e.id] = retry(e, now, u.error ?? `Upload failed (${u.status ?? 'no answer'})`, 'hashed')
    }
  }
  // Uploads queued by a background pass (app closed) for items the ledger hasn't got to yet: the check
  // will find them archived, so the results are only tidied up.
  for (const [id, u] of states) {
    if (entries[id]?.state !== 'uploading' && (u.state === 'done' || u.state === 'failed')) finished.push(id)
  }
  if (finished.length) await ctx.phone.forget(finished)
  return { ...ledger, entries }
}

function message(err: unknown): string { return err instanceof Error ? err.message : String(err) }
