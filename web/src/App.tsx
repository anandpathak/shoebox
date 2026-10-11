import { Shell } from './components/Shell'
import { AppProvider } from './lib/app'
import { useRoute } from './lib/router'
import { Library } from './screens/Library'
import { People } from './screens/People'
import { Sync } from './screens/Sync'
import { Uploads } from './screens/Uploads'
import { Viewer } from './screens/Viewer'

function Screens() {
  const route = useRoute()
  const tab = route.name === 'media' ? 'library' : route.name
  return (
    <Shell current={tab}>
      {tab === 'people' ? <People />
        : tab === 'sync' ? <Sync />
        : tab === 'uploads' ? <Uploads />
        : <Library route={route} />}
      {route.name === 'media' && route.params[0] && <Viewer route={route} />}
    </Shell>
  )
}

export default function App() {
  return <AppProvider><Screens /></AppProvider>
}
