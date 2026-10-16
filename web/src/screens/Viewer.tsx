import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { MediaDetail, OriginalStatus } from '../api/types'
import { Icon } from '../components/Icon'
import { useApp } from '../lib/app'
import { bytes, dateLabel, duration, place } from '../lib/format'
import { neighbours } from '../lib/listCache'
import { go, type Route } from '../lib/router'
import { isSaved, saveOffline, unsaveOffline } from '../local/offline'
import { Editor } from './ViewerEdit'

export function Viewer({ route }: { route: Route }) {
  const { source, version, changed } = useApp()
  const id = route.params[0]
  const listKey = route.query.toString() ? `?${route.query.toString()}` : ''
  const [item, setItem] = useState<MediaDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [chrome, setChrome] = useState(true)                 // top and bottom bars (tap the photo to hide)
  const [details, setDetails] = useState(false)              // the details sheet; stays open while swiping
  const [fav, setFav] = useState(false)
  const [removed, setRemoved] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const { prev, next } = neighbours(listKey, id)

  const load = useCallback(() => {
    source.media(id).then((m) => { setItem(m); setFav(!!m.favorite); setRemoved(!!m.deleted); setError(null) })
      .catch((e) => setError(e.message))
  }, [source, id])
  useEffect(load, [load, version])
  useEffect(() => { setNotice(null) }, [id])

  const close = useCallback(() => go(`library${listKey}`), [listKey])
  const step = useCallback((to: string | null) => { if (to) go(`media/${to}${listKey}`, true) }, [listKey])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest('input, textarea')) return
      if (e.key === 'Escape') { if (details) setDetails(false); else close() }
      if (e.key === 'ArrowLeft') step(prev)
      if (e.key === 'ArrowRight') step(next)
      if (e.key === 'i') setDetails((d) => !d)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [close, step, prev, next, details])

  // swipe left/right to move; a tap shows or hides the bars
  const start = useRef<{ x: number; y: number; t: number } | null>(null)
  const onPointerDown = (e: React.PointerEvent) => { start.current = { x: e.clientX, y: e.clientY, t: Date.now() } }
  const onPointerUp = (e: React.PointerEvent) => {
    const s = start.current
    start.current = null
    if (!s) return
    const dx = e.clientX - s.x, dy = e.clientY - s.y
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) { step(dx < 0 ? next : prev); return }
    const tap = Math.abs(dx) < 10 && Math.abs(dy) < 10 && Date.now() - s.t < 350
    if (tap && item?.media_type !== 'video' && !(e.target as HTMLElement).closest('button')) setChrome((c) => !c)
  }

  const toggleFav = async () => {
    if (!item) return
    const want = !fav
    setFav(want)
    try { await source.edit.patch(item.photo_id, { favorite: want }); changed() } catch (e) { setFav(!want); setNotice((e as Error).message) }
  }
  const remove = async () => {
    if (!item) return
    try { await source.edit.remove(item.photo_id); setRemoved(true); setDetails(false); changed() }
    catch (e) { setNotice((e as Error).message) }
  }
  const undo = async () => {
    if (!item) return
    try { await source.edit.restore(item.photo_id); setRemoved(false); load(); changed() } catch (e) { setNotice((e as Error).message) }
  }

  const title = item ? (item.title || dateLabel(item.taken_at)) : ''
  const where = item ? (item.place_name || place(item)) : ''
  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label="Photo viewer" data-chrome={chrome ? 'on' : 'off'}
         data-details={details ? 'on' : 'off'}>
      <header className="vtop">
        <button className="vicon" onClick={close} aria-label="Close"><Icon name="back" /></button>
        <div className="vtitle"><strong>{title}</strong>{where && <span>{where}</span>}</div>
        <span className="vspacer" />
      </header>

      <div className="viewer-media" onPointerDown={onPointerDown} onPointerUp={onPointerUp}>
        {prev && <button className="vbtn prev" onClick={() => step(prev)} aria-label="Previous"><Icon name="left" /></button>}
        {next && <button className="vbtn next" onClick={() => step(next)} aria-label="Next"><Icon name="right" /></button>}
        {error && <div className="error">{error}</div>}
        {item && (item.media_type === 'video'
          ? <video key={id} src={source.compressedUrl(id)} poster={item.poster_key ? source.posterUrl(id) : undefined}
                   controls playsInline preload="metadata" />
          : <img key={id} src={item.compressed_key ? source.compressedUrl(id) : source.thumbUrl(id)} alt={item.title ?? ''}
                 draggable={false} />)}
        {removed && (
          <div className="vtoast" role="status">
            <strong>Removed from your library</strong>
            <span>The original stays safe in S3.</span>
            <div className="row">
              <button className="btn" onClick={undo}>Undo</button>
              <button className="btn" onClick={close}>Back to library</button>
            </div>
          </div>
        )}
        {notice && <div className="vtoast error" role="alert" onClick={() => setNotice(null)}>{notice}</div>}
      </div>

      {item && (
        <nav className="vbar" aria-label="Photo actions">
          {source.canEdit && (
            <button className="vaction" aria-pressed={fav} onClick={toggleFav} disabled={removed}
                    aria-label={fav ? 'Favourite' : 'Add to favourites'}>
              <Icon name="heart" filled={fav} /><span>Favourite</span></button>
          )}
          <button className="vaction" aria-pressed={details} onClick={() => setDetails((d) => !d)} aria-label="Details">
            <Icon name="info" /><span>Details</span></button>
          {item.compressed_key && (
            <a className="vaction" href={source.compressedUrl(item.photo_id, true)} download
               aria-label={`Download ${item.media_type === 'video' ? 'video' : 'photo'}`}>
              <Icon name="download" /><span>Download</span></a>
          )}
          {canShare() && item.compressed_key && (
            <button className="vaction" onClick={() => share(source.compressedUrl(item.photo_id), item).catch((e) => setNotice(e.message))}
                    aria-label="Share"><Icon name="share" /><span>Share</span></button>
          )}
          {source.canEdit && !removed && (
            <button className="vaction danger" onClick={remove} aria-label="Remove"><Icon name="trash" /><span>Remove</span></button>
          )}
        </nav>
      )}

      {item && <Sheet open={details} onClose={() => setDetails(false)}><Details item={item} onChanged={load} /></Sheet>}
    </div>
  )
}

function canShare(): boolean {
  return typeof navigator !== 'undefined' && typeof navigator.share === 'function'
}

/** The phone's own share sheet, with the picture itself when it can take files (else a link). */
async function share(url: string, item: MediaDetail): Promise<void> {
  const name = (item.orig_name ?? item.photo_id).replace(/\.[^.]+$/, '') + (item.media_type === 'video' ? '.mp4' : '.jpg')
  try {
    const blob = await (await fetch(url)).blob()
    const file = new File([blob], name, { type: blob.type || (item.media_type === 'video' ? 'video/mp4' : 'image/jpeg') })
    if (navigator.canShare?.({ files: [file] })) { await navigator.share({ files: [file], title: item.title ?? undefined }); return }
  } catch (e) {
    if ((e as Error).name === 'AbortError') return            // the person closed the share sheet
  }
  await navigator.share({ url, title: item.title ?? undefined }).catch((e) => { if (e.name !== 'AbortError') throw e })
}

/** A sheet over the photo: slides up from the bottom on phones, in from the right on wide screens. Close with
 * the ✕, a tap on the dimmed photo, Esc, or by dragging the handle down. */
function Sheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: React.ReactNode }) {
  const [drag, setDrag] = useState(0)
  const from = useRef<number | null>(null)
  return (
    <>
      <div className="sheet-backdrop" data-open={open} onClick={onClose} aria-hidden="true" />
      <section className="sheet" data-open={open} role="dialog" aria-label="Details" aria-hidden={!open}
               style={drag ? { transform: `translateY(${drag}px)`, transition: 'none' } : undefined} inert={!open}>
        <div className="sheet-head"
             onPointerDown={(e) => { from.current = e.clientY; (e.target as HTMLElement).setPointerCapture?.(e.pointerId) }}
             onPointerMove={(e) => { if (from.current != null) setDrag(Math.max(0, e.clientY - from.current)) }}
             onPointerUp={() => { if (drag > 90) onClose(); from.current = null; setDrag(0) }}
             onPointerCancel={() => { from.current = null; setDrag(0) }}>
          <span className="grabber" aria-hidden="true" />
          <h2>Details</h2>
          <button className="vicon soft" onClick={onClose} aria-label="Close details"><Icon name="close" size={20} /></button>
        </div>
        <div className="sheet-body">{children}</div>
      </section>
    </>
  )
}

function Details({ item, onChanged }: { item: MediaDetail; onChanged: () => void }) {
  const { source } = useApp()
  const map = item.lat != null && item.lon != null
    ? `https://www.openstreetmap.org/?mlat=${item.lat}&mlon=${item.lon}#map=15/${item.lat}/${item.lon}` : null
  const camera = [item.camera_make, item.camera_model].filter(Boolean).join(' ')
  return (
    <div className="viewer-info">
      <Editor item={item} onChanged={onChanged} />
      <MetadataNote item={item} />
      <section className="sheet-section">
      <h3>About this photo</h3>
      <dl className="facts card-facts">
        <dt>Taken</dt><dd>{dateLabel(item.taken_at)}</dd>
        {(place(item) || map) && <><dt>Place</dt><dd data-testid="place">{item.place_name || place(item) || coords(item.lat!, item.lon!)}
          {map && <> · <a href={map} target="_blank" rel="noreferrer">Map</a></>}</dd></>}
        {camera && <><dt>Camera</dt><dd>{camera}</dd></>}
        {item.media_type === 'video' && item.duration_s != null && <><dt>Length</dt><dd>{duration(item.duration_s)}</dd></>}
        {item.albums.length > 0 && <><dt>Albums</dt><dd>{item.albums.join(', ')}</dd></>}
        <dt>From</dt><dd>{item.sources.map((s) => s.name ?? s.device_id).join(' · ') || '—'}</dd>
        <dt>Sizes</dt><dd>{[item.bytes_compressed && `${bytes(item.bytes_compressed)} copy`, item.bytes_raw && `${bytes(item.bytes_raw)} original`].filter(Boolean).join(' · ')}</dd>
        <dt>File</dt><dd className="mono">{item.orig_name ?? '—'}</dd>
      </dl>
      </section>
      {source.kind !== 'cloud' && (
        <div className="sheet-actions sheet-section">
          <h3>Original and offline</h3>
          <SaveOfflineButton id={item.photo_id} />
          <OriginalButton id={item.photo_id} />
        </div>
      )}
      <p className="mono muted small">{item.photo_id}</p>
    </div>
  )
}

function SaveOfflineButton({ id }: { id: string }) {
  const [saved, setSaved] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { isSaved(id).then(setSaved).catch(() => setSaved(null)) }, [id])
  if (saved === null || typeof caches === 'undefined') return null
  const toggle = async () => {
    setBusy(true)
    try { if (saved) await unsaveOffline(id); else await saveOffline(id); setSaved(!saved) } catch { /* shown via state */ } finally { setBusy(false) }
  }
  return <button className="btn" aria-pressed={saved} disabled={busy} onClick={toggle}>{saved ? 'Saved offline' : 'Save offline'}</button>
}

function OriginalButton({ id }: { id: string }) {
  const [st, setSt] = useState<OriginalStatus | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setSt(null); setErr(null) }, [id])
  const request = () => {
    setBusy(true)
    api<OriginalStatus>(`/api/media/${id}/original`, { method: 'POST', json: { tier: 'standard' } })
      .then(setSt).catch((e) => setErr(e.message)).finally(() => setBusy(false))
  }
  if (st?.url) return <a className="btn" href={st.url}><Icon name="archive" size={18} />Download original</a>
  return (
    <div className="original">
      <button className="btn" onClick={request} disabled={busy}><Icon name="archive" size={18} />
        {st?.status === 'in_progress' ? 'Original requested' : 'Request original'}</button>
      <span className="muted small">
        {err ?? (st?.status === 'in_progress' ? `Ready within about ${st.eta_hours} hours. Check back later.`
          : 'Originals are in Deep Archive: getting one back takes about 12 hours.')}
      </span>
    </div>
  )
}

export function coords(lat: number, lon: number): string {
  return `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? 'N' : 'S'}, ${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? 'E' : 'W'}`
}

// Phones can strip location (and sometimes the capture date) from what they hand over; say why and how to fix it.
export function metadataNotes(item: Pick<MediaDetail, 'sources' | 'lat' | 'taken_at_source'>): string[] {
  const fromPhone = item.sources.filter((s) => /^(upload|ios|android):/.test(s.source_ref))
  if (!fromPhone.length) return []
  const notes: string[] = []
  if (item.lat == null) {
    const s = fromPhone[0]
    const android = s.platform === 'android' || s.source_ref.startsWith('android:')
    if (s.source_ref.startsWith('android:')) {
      notes.push('No location: Shoebox may not have had the “location in photos” permission when this was backed up '
        + '(Settings → Apps → Shoebox → Permissions → Photos and videos), or the camera had location off.')
    } else if (s.source_ref.startsWith('ios:')) {
      notes.push('No location: the camera had location off when this was taken.')
    } else if (android) {
      notes.push('No location: Android removes it from photos picked by hand. In the Shoebox app, use Sync → Back up this '
        + 'phone instead; it keeps the location.')
    } else {
      notes.push('No location: the photo picker removed it. On iPhone, turn on Options → Location when picking; the Shoebox '
        + 'app’s automatic backup keeps it.')
    }
  }
  if (item.taken_at_source === 'last_modified') notes.push('The capture date wasn’t in the file, so this uses when the file was last changed.')
  return notes
}

function MetadataNote({ item }: { item: MediaDetail }) {
  const notes = metadataNotes(item)
  if (!notes.length) return null
  return <div className="note-warn" role="note"><strong>Metadata incomplete</strong>{notes.map((n) => <span key={n}>{n}</span>)}</div>
}
