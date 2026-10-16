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
  const { source, version } = useApp()
  const id = route.params[0]
  const listKey = route.query.toString() ? `?${route.query.toString()}` : ''
  const [item, setItem] = useState<MediaDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { prev, next } = neighbours(listKey, id)

  const load = useCallback(() => {
    source.media(id).then((m) => { setItem(m); setError(null) }).catch((e) => setError(e.message))
  }, [source, id])
  useEffect(load, [load, version])

  const close = useCallback(() => go(`library${listKey}`), [listKey])
  const step = useCallback((to: string | null) => { if (to) go(`media/${to}${listKey}`, true) }, [listKey])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest('input, textarea')) return
      if (e.key === 'Escape') close()
      if (e.key === 'ArrowLeft') step(prev)
      if (e.key === 'ArrowRight') step(next)
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [close, step, prev, next])

  // swipe left/right on the media
  const start = useRef<{ x: number; y: number } | null>(null)
  const onPointerDown = (e: React.PointerEvent) => { start.current = { x: e.clientX, y: e.clientY } }
  const onPointerUp = (e: React.PointerEvent) => {
    const s = start.current
    start.current = null
    if (!s) return
    const dx = e.clientX - s.x
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(e.clientY - s.y) * 1.5) step(dx < 0 ? next : prev)
  }

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label="Photo viewer">
      <div className="viewer-media" onPointerDown={onPointerDown} onPointerUp={onPointerUp}>
        <button className="vbtn close" onClick={close} aria-label="Close"><Icon name="close" /></button>
        {prev && <button className="vbtn prev" onClick={() => step(prev)} aria-label="Previous"><Icon name="left" /></button>}
        {next && <button className="vbtn next" onClick={() => step(next)} aria-label="Next"><Icon name="right" /></button>}
        {error && <div className="error">{error}</div>}
        {item && (item.media_type === 'video'
          ? <video key={id} src={source.compressedUrl(id)} poster={item.poster_key ? source.posterUrl(id) : undefined}
                   controls playsInline preload="metadata" />
          : <img key={id} src={item.compressed_key ? source.compressedUrl(id) : source.thumbUrl(id)} alt={item.title ?? ''}
                 draggable={false} />)}
      </div>
      {item && <Details item={item} onChanged={load} />}
    </div>
  )
}

function Details({ item, onChanged }: { item: MediaDetail; onChanged: () => void }) {
  const { source } = useApp()
  const map = item.lat != null && item.lon != null
    ? `https://www.openstreetmap.org/?mlat=${item.lat}&mlon=${item.lon}#map=15/${item.lat}/${item.lon}` : null
  const camera = [item.camera_make, item.camera_model].filter(Boolean).join(' ')
  return (
    <aside className="viewer-info">
      <Editor item={item} onChanged={onChanged} />
      <MetadataNote item={item} />
      <dl className="facts">
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
      <div className="row">
        {item.compressed_key && <a className="btn primary" href={source.compressedUrl(item.photo_id, true)} download>
          <Icon name="download" size={18} />Download {item.media_type === 'video' ? 'video' : 'photo'}</a>}
        {source.kind !== 'cloud' && <SaveOfflineButton id={item.photo_id} />}
        {source.kind !== 'cloud' && <OriginalButton id={item.photo_id} />}
      </div>
      <p className="mono muted">{item.photo_id}</p>
    </aside>
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
