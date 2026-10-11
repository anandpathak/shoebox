import { useMemo } from 'react'
import { Grid } from '../components/Grid'
import { TopBar } from '../components/Shell'
import { useApp } from '../lib/app'
import { go } from '../lib/router'
import { useSearch } from '../lib/useSearch'

export function Library() {
  const { source, version } = useApp()
  const params = useMemo(() => ({}), [])
  const { items, loading, error, done, more } = useSearch(source, params, version)
  return (
    <>
      <TopBar title="Library" />
      {error && <div className="error">{error}</div>}
      {!loading && !error && items.length === 0
        ? <div className="empty"><strong>No photos yet</strong><span>Archive some from Apple Photos and they'll appear here.</span></div>
        : <Grid items={items} source={source} onOpen={(id) => go(`media/${id}`)} onEnd={more} loading={loading} done={done} />}
    </>
  )
}
