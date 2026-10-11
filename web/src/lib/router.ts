// Tiny hash router: #/library, #/media/<id>, #/people, … Works offline and needs no server routes.
import { useEffect, useState } from 'react'

export interface Route { name: string; params: string[]; query: URLSearchParams }

export function parse(hash: string): Route {
  const [path, q] = hash.replace(/^#\/?/, '').split('?')
  const [name = 'library', ...params] = path.split('/').filter(Boolean).map(decodeURIComponent)
  return { name, params, query: new URLSearchParams(q || '') }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.hash))
  useEffect(() => {
    const on = () => setRoute(parse(location.hash))
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])
  return route
}

export function go(path: string, replace = false) {
  const url = `#/${path.replace(/^\/+/, '')}`
  if (replace) history.replaceState(null, '', url)
  else location.hash = url
  if (replace) dispatchEvent(new HashChangeEvent('hashchange'))
}
