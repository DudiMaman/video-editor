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

const TABS = backend.supportsScout
  ? ['batch', 'results', 'assets', 'inbox', 'brief', 'review', 'approved', 'published', 'log']
  : ['batch', 'results', 'assets']

export default function App() {
  const [tab, setTab] = useState('batch')
  const [showSettings, setShowSettings] = useState(
    backend.mode === 'github' && !backend.hasToken()
  )

  return (
    <div className="app">
      <header className="app-header">
        <h1>{STR.appTitle}</h1>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t}
              className={'tab' + (tab === t ? ' active' : '')}
              onClick={() => setTab(t)}
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
