import { Shell, TopBar } from './components/Shell'
import { AppProvider } from './lib/app'
import { useRoute } from './lib/router'
import { Library } from './screens/Library'

function Placeholder({ title }: { title: string }) {
  return <><TopBar title={title} /><div className="empty">Coming in the next step.</div></>
}

function Screens() {
  const route = useRoute()
  const tab = route.name === 'media' ? 'library' : route.name
  return (
    <Shell current={tab}>
      {tab === 'people' ? <Placeholder title="People" />
        : tab === 'sync' ? <Placeholder title="Sync & devices" />
        : <Library />}
    </Shell>
  )
}

export default function App() {
  return <AppProvider><Screens /></AppProvider>
}
