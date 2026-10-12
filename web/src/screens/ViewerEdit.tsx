import { useEffect, useState } from 'react'
import type { MediaDetail } from '../api/types'
import { Icon } from '../components/Icon'
import type { MediaPatch } from '../data/source'
import { useApp } from '../lib/app'
import { dateLabel } from '../lib/format'
import { go } from '../lib/router'

// Edits apply on screen straight away (optimistic) and roll back if the server says no.
export function Editor({ item, onChanged }: { item: MediaDetail; onChanged: () => void }) {
  const { source, changed } = useApp()
  const [local, setLocal] = useState(item)
  const [title, setTitle] = useState(item.title ?? '')
  const [desc, setDesc] = useState(item.description ?? '')
  const [newTag, setNewTag] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [deleted, setDeleted] = useState(false)
  // Only save what the user typed: if the item changes underneath (sync, reconnect), never write the old text back.
  const [dirty, setDirty] = useState<{ title?: boolean; desc?: boolean }>({})
  useEffect(() => {
    setLocal(item); setTitle(item.title ?? ''); setDesc(item.description ?? ''); setDeleted(!!item.deleted); setDirty({})
  }, [item])

  async function run(optimistic: Partial<MediaDetail>, action: () => Promise<unknown>) {
    const before = local
    setLocal({ ...local, ...optimistic })
    setError(null)
    try {
      const res = await action()
      if (res && typeof res === 'object' && 'photo_id' in res) setLocal(res as MediaDetail)
      changed()
    } catch (e) {
      setLocal(before)
      setError((e as Error).message)
    }
  }
  const patch = (p: MediaPatch, opt: Partial<MediaDetail>) => run(opt, () => source.edit.patch(item.photo_id, p))

  if (!source.canEdit) return <h2>{item.title || dateLabel(item.taken_at)}</h2>
  if (deleted) {
    return (
      <div className="editor card">
        <strong>Removed from your library</strong>
        <span className="muted small">The original stays safe in S3.</span>
        <div className="row">
          <button className="btn" onClick={() => run({}, async () => { await source.edit.restore(item.photo_id); setDeleted(false); onChanged() })}>Undo</button>
          <button className="btn" onClick={() => go('library')}>Back to library</button>
        </div>
      </div>
    )
  }
  return (
    <div className="editor">
      <input className="title-input" type="text" value={title} placeholder={dateLabel(item.taken_at)} aria-label="Title"
             onChange={(e) => { setTitle(e.target.value); setDirty((d) => ({ ...d, title: true })) }}
             onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
             onBlur={() => {
               if (dirty.title && title !== (local.title ?? '')) patch({ title: title || null }, { title: title || null })
               setDirty((d) => ({ ...d, title: false }))
             }} />
      <textarea className="desc-input" value={desc} placeholder="Add a description" aria-label="Description" rows={2}
                onChange={(e) => { setDesc(e.target.value); setDirty((d) => ({ ...d, desc: true })) }}
                onBlur={() => {
                  if (dirty.desc && desc !== (local.description ?? '')) patch({ description: desc || null }, { description: desc || null })
                  setDirty((d) => ({ ...d, desc: false }))
                }} />
      <div className="row">
        <button className="btn" aria-pressed={!!local.favorite} onClick={() => patch({ favorite: !local.favorite }, { favorite: local.favorite ? 0 : 1 })}>
          <Icon name="heart" size={18} filled={!!local.favorite} />{local.favorite ? 'Favourite' : 'Add to favourites'}</button>
        <button className="btn" aria-pressed={!!local.hidden} onClick={() => patch({ hidden: !local.hidden }, { hidden: local.hidden ? 0 : 1 })}>
          <Icon name="eye" size={18} />{local.hidden ? 'Hidden' : 'Hide'}</button>
        <button className="btn danger" onClick={() => run({}, async () => { await source.edit.remove(item.photo_id); setDeleted(true) })}>
          <Icon name="trash" size={18} />Remove</button>
      </div>
      {local.people.length > 0 && <div className="row">{local.people.map((p) => <span key={p.person_id} className="tag person">{p.name}</span>)}</div>}
      <div className="row tags" aria-label="Tags">
        {local.tags.map((t) => (
          <span key={t} className="tag">{t}
            <button aria-label={`Remove tag ${t}`} onClick={() => run({ tags: local.tags.filter((x) => x !== t) }, () => source.edit.removeTag(item.photo_id, t))}>×</button>
          </span>
        ))}
        <form onSubmit={(e) => {
          e.preventDefault()
          const t = newTag.trim()
          if (!t) return
          setNewTag('')
          run({ tags: [...new Set([...local.tags, t.toLowerCase()])].sort() }, () => source.edit.addTag(item.photo_id, t))
        }}>
          <input type="text" value={newTag} onChange={(e) => setNewTag(e.target.value)} placeholder="Add tag" aria-label="Add tag" className="tag-input" />
        </form>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  )
}
