import type { MediaDetail } from '../api/types'
import { dateLabel } from '../lib/format'

// Read-only header for now; editing arrives in T6.6.
export function Editor({ item }: { item: MediaDetail; onChanged: () => void }) {
  return (
    <div className="editor">
      <h2>{item.title || dateLabel(item.taken_at)}</h2>
      {item.people.length > 0 && <div className="row">{item.people.map((p) => <span key={p.person_id} className="tag">{p.name}</span>)}</div>}
      {item.tags.length > 0 && <div className="row">{item.tags.map((t) => <span key={t} className="tag">{t}</span>)}</div>}
    </div>
  )
}
