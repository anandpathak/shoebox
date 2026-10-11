// Paged search with keyset cursors: first page on change, more pages on demand.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { MediaItem, ParsedQuery } from '../api/types'
import type { DataSource, SearchParams } from '../data/source'

export function useSearch(source: DataSource, params: SearchParams, version: number) {
  const [items, setItems] = useState<MediaItem[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [query, setQuery] = useState<ParsedQuery | undefined>()
  const key = JSON.stringify(params)
  const gen = useRef(0)

  useEffect(() => {
    const my = ++gen.current
    setLoading(true)
    setError(null)
    source.search({ ...params, limit: 120 })
      .then((r) => { if (my !== gen.current) return; setItems(r.items); setNext(r.next); setDone(!r.next); setQuery(r.query) })
      .catch((e) => { if (my === gen.current) setError(e.message) })
      .finally(() => { if (my === gen.current) setLoading(false) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, key, version])

  const more = useCallback(() => {
    if (loading || !next) return
    const my = gen.current
    setLoading(true)
    source.search({ ...params, cursor: next, limit: 120 })
      .then((r) => { if (my !== gen.current) return; setItems((xs) => [...xs, ...r.items]); setNext(r.next); setDone(!r.next) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, key, next, loading])

  return { items, loading, error, done, more, query }
}
