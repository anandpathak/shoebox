// The ids the user is browsing, so the viewer can step to the previous/next item.
let current: { key: string; ids: string[] } = { key: '', ids: [] }

export function rememberList(key: string, ids: string[]) {
  current = { key, ids }
}

export function neighbours(key: string, id: string): { prev: string | null; next: string | null } {
  if (current.key !== key) return { prev: null, next: null }
  const i = current.ids.indexOf(id)
  if (i < 0) return { prev: null, next: null }
  return { prev: current.ids[i - 1] ?? null, next: current.ids[i + 1] ?? null }
}
