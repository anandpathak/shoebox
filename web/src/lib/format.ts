// Formatting helpers shared by screens.
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
  'November', 'December']

export function monthKey(takenAt: string | null): string {
  return takenAt ? takenAt.slice(0, 7) : 'unknown'
}

export function monthLabel(key: string): string {
  if (key === 'unknown') return 'Date unknown'
  const [y, m] = key.split('-')
  return `${MONTHS[Number(m) - 1]} ${y}`
}

export function dateLabel(takenAt: string | null): string {
  if (!takenAt) return 'Date unknown'
  const [y, m, d] = takenAt.slice(0, 10).split('-')
  const time = takenAt.length > 15 ? ` · ${takenAt.slice(11, 16)}` : ''
  return `${Number(d)} ${MONTHS[Number(m) - 1].slice(0, 3)} ${y}${time}`
}

export function duration(seconds: number | null): string {
  if (seconds == null) return ''
  const s = Math.round(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v < 10 && i ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}

export function place(item: { place_city?: string | null; place_name?: string | null; place_country?: string | null }): string {
  return [item.place_city || item.place_name, item.place_country].filter(Boolean).join(', ')
}

export function groupByMonth<T extends { taken_at: string | null }>(items: T[]): { key: string; items: T[] }[] {
  const out: { key: string; items: T[] }[] = []
  for (const it of items) {
    const k = monthKey(it.taken_at)
    const last = out[out.length - 1]
    if (last && last.key === k) last.items.push(it)
    else out.push({ key: k, items: [it] })
  }
  return out
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'never'
  const mins = Math.round((now - Date.parse(iso)) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const h = Math.round(mins / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.round(h / 24)} days ago`
}
