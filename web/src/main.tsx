import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/familjen-grotesk/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import './index.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './local/client'
import { startAutomaticBackup } from './backup/runner'
import { isViewerMode } from './cloud/mode'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

const viewer = isViewerMode()
if (!viewer) startAutomaticBackup()     // only does something inside the iPhone/Android app

if (!viewer && 'serviceWorker' in navigator && import.meta.env.PROD) {
  addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}) })
}
