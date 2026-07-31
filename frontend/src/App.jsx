import { useState } from 'react'
import { STR } from './strings.js'
import { backend } from './backend/index.js'
import BatchBuilder from './components/BatchBuilder.jsx'
import ResultsTab from './components/ResultsTab.jsx'
import AssetsTab from './components/AssetsTab.jsx'
import TokenPanel from './components/TokenPanel.jsx'

const TABS = ['batch', 'results', 'assets']

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
      </main>
    </div>
  )
}
