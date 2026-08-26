import { useCallback, useRef, useState } from 'react'
import './App.css'
import { api } from './api'
import ToastStack from './components/Toast'
import FunnelScreen from './screens/FunnelScreen'
import DayStoryScreen from './screens/DayStoryScreen'
import EntityTimelineScreen from './screens/EntityTimelineScreen'
import TrustCurvesScreen from './screens/TrustCurvesScreen'
import HumanReviewScreen from './screens/HumanReviewScreen'
import SystemHealthScreen from './screens/SystemHealthScreen'

const TABS = [
  { key: 'funnel', label: 'Funnel', component: FunnelScreen },
  { key: 'daystory', label: 'Day Story', component: DayStoryScreen },
  { key: 'timeline', label: 'Entity Timeline', component: EntityTimelineScreen },
  { key: 'trust', label: 'Trust Curves', component: TrustCurvesScreen },
  { key: 'review', label: 'Human Review', component: HumanReviewScreen },
  { key: 'health', label: 'System Health', component: SystemHealthScreen },
]

function App() {
  const [tab, setTab] = useState('funnel')
  const [toasts, setToasts] = useState([])
  const [advancing, setAdvancing] = useState(false)
  // The day a successful /advance just simulated, lifted here so the time-warp
  // buttons can hand the Day Story screen the day it should open on.
  const [storyDay, setStoryDay] = useState(null)
  const toastSeq = useRef(0)

  const pushToast = useCallback((text, kind = 'info') => {
    const id = ++toastSeq.current
    setToasts((prev) => [...prev, { id, text, kind }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4200)
  }, [])

  const handleAdvance = useCallback(
    async (days) => {
      setAdvancing(true)
      try {
        const result = await api.advance(days)
        // /advance now returns the story for each day it just simulated
        // (packet P10). Land on the LAST one — the day that just happened —
        // so pressing the button shows what it did instead of only moving a
        // counter. `result.day` is days ELAPSED, so the last simulated index
        // is one less; the stories keys are the authority when present.
        const days_run = Object.keys(result?.stories || {}).map(Number)
        const landed = days_run.length > 0 ? Math.max(...days_run) : (result?.day ?? 1) - 1
        setStoryDay(Math.max(0, landed))
        setTab('daystory')
        pushToast(`Advanced ${days} day${days === 1 ? '' : 's'} — showing day ${Math.max(0, landed)}.`, 'success')
      } catch (e) {
        // /advance doesn't exist yet — a later packet adds the integration
        // runner. Never let this crash the dashboard.
        if (e.status === 404 || e.status === 0 || !e.status) {
          pushToast('Time-warp integration runner coming online — /advance is not wired yet.', 'info')
        } else {
          pushToast(`Advance failed: ${e.message}`, 'error')
        }
      } finally {
        setAdvancing(false)
      }
    },
    [pushToast],
  )

  const ActiveScreen = TABS.find((t) => t.key === tab)?.component || FunnelScreen

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <span className="app-title">Promise Keeper</span>
          <span className="app-subtitle">Judge Dashboard</span>
        </div>
        <div className="time-warp">
          <button type="button" className="btn btn-timewarp" disabled={advancing} onClick={() => handleAdvance(1)}>
            Advance 1 Day ▶
          </button>
          <button type="button" className="btn btn-timewarp btn-timewarp-fast" disabled={advancing} onClick={() => handleAdvance(45)}>
            Run to Day 45 ⏩
          </button>
        </div>
      </header>

      <div className="app-body">
        <nav className="app-sidebar">
          {TABS.map((t) => (
            <button key={t.key} type="button" className={`sidebar-tab ${tab === t.key ? 'sidebar-tab-active' : ''}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </nav>
        <main className="app-main">
          <ActiveScreen dayHint={storyDay} />
        </main>
      </div>

      <ToastStack toasts={toasts} />
    </div>
  )
}

export default App
