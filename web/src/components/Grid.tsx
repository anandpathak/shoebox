import { useEffect, useRef } from 'react'
import type { MediaItem } from '../api/types'
import type { DataSource } from '../data/source'
import { duration, groupByMonth, monthLabel, place } from '../lib/format'
import { Icon } from './Icon'

export function Tile({ item, source, onOpen }: { item: MediaItem; source: DataSource; onOpen: (id: string) => void }) {
  const label = [item.title, place(item), item.taken_at?.slice(0, 10)].filter(Boolean).join(' · ') || 'Photo'
  return (
    <button className="tile" onClick={() => onOpen(item.photo_id)} aria-label={label} data-id={item.photo_id}>
      {item.thumb_key ? <img src={source.thumbUrl(item.photo_id)} alt="" loading="lazy" decoding="async" />
        : <span className="tile-missing">No preview</span>}
      {item.media_type === 'video' && <span className="badge"><Icon name="play" size={10} filled />{duration(item.duration_s)}</span>}
      {item.favorite ? <span className="fav"><Icon name="heart" size={14} filled /></span> : null}
      {item.devices.length > 1 && <span className="multi" title={`On ${item.devices.length} devices`}>{item.devices.length}</span>}
    </button>
  )
}

export function Grid({ items, source, onOpen, onEnd, loading, done }: {
  items: MediaItem[]; source: DataSource; onOpen: (id: string) => void; onEnd: () => void; loading: boolean; done: boolean
}) {
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) onEnd() }, { rootMargin: '1200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [onEnd])
  return (
    <div className="months">
      {groupByMonth(items).map((g) => (
        <section key={g.key} className="month" style={{ containIntrinsicSize: `auto ${Math.ceil(g.items.length / 3) * 130}px` }}>
          <h2 className="month-title">{monthLabel(g.key)} <span className="muted small">{g.items.length}</span></h2>
          <div className="grid">{g.items.map((it) => <Tile key={it.photo_id} item={it} source={source} onOpen={onOpen} />)}</div>
        </section>
      ))}
      <div ref={sentinel} className="sentinel">{loading ? 'Loading…' : done && items.length ? 'That’s everything.' : ''}</div>
    </div>
  )
}
