import { useEffect, useState } from 'react'
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
import ZernioAccountsTab from './components/ZernioAccountsTab.jsx'
import ZernioInboxTab from './components/ZernioInboxTab.jsx'

// All tabs are shown directly in the bar (the bar wraps into extra rows
// on narrow screens; each tab label stays on one line).
const TABS = backend.supportsScout
  ? ['inbox', 'review', 'batch', 'approved', 'published', 'assets', 'zernio', 'zernioInbox', 'results', 'brief', 'log', 'aimodels']
  : ['batch', 'results', 'assets', 'zernio', 'zernioInbox', 'aimodels']

export default function App() {
  const [tab, setTab] = useState(TABS[0])
  const [showSettings, setShowSettings] = useState(
    backend.mode === 'github' && !backend.hasToken()
  )
  const [tokenOk, setTokenOk] = useState(
    backend.mode !== 'github' || backend.hasToken()
  )

  // Any write attempted without a token (the token lives only in this
  // browser's local storage) reopens the settings panel - the fix is one
  // paste away instead of an error toast plus a hunt for the gear.
  useEffect(() => {
    const onMissing = () => {
      setTokenOk(false)
      setShowSettings(true)
    }
    window.addEventListener('ve-token-missing', onMissing)
    return () => window.removeEventListener('ve-token-missing', onMissing)
  }, [])

  const closeSettings = () => {
    setShowSettings(false)
    setTokenOk(backend.mode !== 'github' || backend.hasToken())
  }

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
            // Settings/token as a labeled tab right after "דמויות AI"
            // (the last tab). A full-label tab is findable in the wrapping
            // bar where the bare ⚙ was not; turns red when no token is
            // stored in this browser.
            <button
              className={'tab' + (showSettings ? ' active' : '')}
              onClick={() => (showSettings ? closeSettings() : setShowSettings(true))}
              title={tokenOk ? STR.github.settingsTitle : STR.github.needToken}
              style={tokenOk ? undefined : { color: '#d33', fontWeight: 700 }}
            >
              ⚙ {STR.github.settingsBtn}{tokenOk ? '' : ' ⚠️'}
            </button>
          )}
        </nav>
      </header>
      <main>
        {showSettings && <TokenPanel onClose={closeSettings} />}
        <div hidden={tab !== 'batch'}>
          <BatchBuilder active={tab === 'batch'} onSent={() => setTab('results')} />
        </div>
        <div hidden={tab !== 'results'}>
          <ResultsTab active={tab === 'results'} />
        </div>
        <div hidden={tab !== 'assets'}>
          <AssetsTab active={tab === 'assets'} />
        </div>
        <div hidden={tab !== 'zernio'}>
          <ZernioAccountsTab active={tab === 'zernio'} />
        </div>
        <div hidden={tab !== 'zernioInbox'}>
          <ZernioInboxTab active={tab === 'zernioInbox'} />
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
