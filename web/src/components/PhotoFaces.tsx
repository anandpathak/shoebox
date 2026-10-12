// Faces the Mac found in this photo: who they are, "Not them" for a wrong match, or a name for an unknown face.
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'

interface FaceInfo { face_id: number; box: number[]; status: string; person_id: string | null; name: string | null }

export function PhotoFaces({ photoId, onChanged }: { photoId: string; onChanged: () => void }) {
  const [faces, setFaces] = useState<FaceInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    api<FaceInfo[]>(`/api/media/${encodeURIComponent(photoId)}/faces`).then(setFaces).catch(() => setFaces([]))
  }, [photoId])
  useEffect(() => { load() }, [load])
  if (!faces.length) return null
  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn(); load(); onChanged() } catch (e) { setError((e as Error).message) }
  }
  return (
    <div className="photo-faces" aria-label="Faces in this photo">
      {faces.map((f) => (
        <div key={f.face_id} className="photo-face" data-testid="photo-face">
          <img src={`/api/faces/${f.face_id}.jpg`} alt="" loading="lazy" />
          {f.name
            ? <>
                <span>{f.name}{f.status === 'auto' && <span className="muted small"> · recognised</span>}</span>
                <button className="link small" onClick={() => act(() => api(`/api/faces/${f.face_id}/not-them`, { method: 'POST' }))}>
                  Not {f.name}</button>
              </>
            : <NameFace onSave={(name) => act(() => api('/api/faces/name', { method: 'POST', json: { face_ids: [f.face_id], name } }))} />}
        </div>
      ))}
      {error && <div className="error small">{error}</div>}
    </div>
  )
}

function NameFace({ onSave }: { onSave: (name: string) => void }) {
  const [name, setName] = useState('')
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onSave(name.trim()) }}>
      <input type="text" list="people-names" value={name} onChange={(e) => setName(e.target.value)} placeholder="Who's this?"
             aria-label="Name this face" />
      <button className="btn" disabled={!name.trim()}>Save</button>
    </form>
  )
}
