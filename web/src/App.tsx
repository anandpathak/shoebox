import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { Health } from './api/types'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api<Health>('/api/health').then(setHealth).catch((e) => setError(e.message))
  }, [])
  return (
    <main>
      <h1>Photoframing</h1>
      {health && <p>Connected to {health.device.name}</p>}
      {error && <p role="alert">{error}</p>}
    </main>
  )
}
