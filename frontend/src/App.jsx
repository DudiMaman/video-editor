import { useState } from 'react'
import { STR } from './strings.js'
import { backend } from './backend/index.js'
import BatchBuilder from './components/BatchBuilder.jsx'
import ResultsTab from './components/ResultsTab.jsx'
import AssetsTab from './components/AssetsTab.jsx'
import TokenPanel from './components/TokenPanel.jsx'
import InboxTab from './components/InboxTab.jsx'
import BriefTab from './components/BriefTab.jsx'
import ReviewTab from './components/ReviewTab.jsx'
import LogTab from './components/LogTab.jsx'
import AiModelsTab from './components/AiModelsTab.jsx'

// All tabs are shown directly in the bar (the bar wraps into extra rows
// on narrow screens; each tab label stays on one line).
const TABS = backend.supportsScout
  ? ['inbox', 'review', 'batch', 'approved', 'published', 'assets', 'results', 'brief', 'log', 'aimodels']
  : ['batch', 'results', 'assets', 'aimodels']

export default function App() {
  const [tab, setTab] = useState(TABS[0])
  const [showSettings, setShowSettings] = useState(
    backend.mode === 'github' && !backend.hasToken()
  )

  const pick = (t) => {
    setTab(t)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>{STR.appTitle}</h1>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t}
              className={'tab' + (tab === t ? ' active' : '')}
              onClick={() => pick(t)}
            >
              {STR.tabs[t]}
            </button>
          ))}
          {backend.mode === 'github' && (
            <button
              className={'tab' + (showSettings ? ' active' : '')}
              onClick={() => setShowSettings((v) => !v)}
              title={STR.github.settingsTitle}
            >
              ⚙
            </button>
          )}
        </nav>
      </header>
      <main>
        {showSettings && <TokenPanel onClose={() => setShowSettings(false)} />}
        <div hidden={tab !== 'batch'}>
          <BatchBuilder active={tab === 'batch'} onSent={() => setTab('results')} />
        </div>
        <div hidden={tab !== 'results'}>
          <ResultsTab active={tab === 'results'} />
        </div>
        <div hidden={tab !== 'assets'}>
          <AssetsTab active={tab === 'assets'} />
        </div>
        <div hidden={tab !== 'aimodels'}>
          <AiModelsTab active={tab === 'aimodels'} />
        </div>
        {backend.supportsScout && (
          <>
            <div hidden={tab !== 'inbox'}>
              <InboxTab active={tab === 'inbox'} />
            </div>
            <div hidden={tab !== 'brief'}>
              <BriefTab active={tab === 'brief'} />
            </div>
            <div hidden={tab !== 'review'}>
              <ReviewTab active={tab === 'review'} stage="pending" />
            </div>
            <div hidden={tab !== 'approved'}>
              <ReviewTab active={tab === 'approved'} stage="approved" />
            </div>
            <div hidden={tab !== 'published'}>
              <ReviewTab active={tab === 'published'} stage="published" />
            </div>
            <div hidden={tab !== 'log'}>
              <LogTab active={tab === 'log'} />
            </div>
          </>
        )}
      </main>
    </div>
  )
}
