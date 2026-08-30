import { useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import {
  getAnthropicKey,
  setAnthropicKey,
  clearAnthropicKey,
} from '../captionsClient.js'

export default function TokenPanel({ onClose }) {
  const [value, setValue] = useState('')
  const [hasToken, setHasToken] = useState(backend.hasToken())
  const [keyValue, setKeyValue] = useState('')
  const [hasKey, setHasKey] = useState(!!getAnthropicKey())
  const [linkNote, setLinkNote] = useState('')

  const save = () => {
    if (!value.trim()) return
    backend.setToken(value)
    setValue('')
    setHasToken(true)
  }

  const copyQuickLink = async () => {
    const url = backend.tokenLink && backend.tokenLink()
    if (!url) return
    try { await navigator.clipboard.writeText(url) } catch { /* clipboard blocked */ }
    setLinkNote(STR.github.quickLinkCopied)
    setTimeout(() => setLinkNote(''), 4000)
  }

  const clear = () => {
    backend.clearToken()
    setHasToken(false)
  }

  const saveKey = () => {
    if (!keyValue.trim()) return
    setAnthropicKey(keyValue)
    setKeyValue('')
    setHasKey(true)
  }

  const clearKey = () => {
    clearAnthropicKey()
    setHasKey(false)
  }

  return (
    <div className="card token-panel">
      <h3>{STR.github.settingsTitle}</h3>
      <p className="hint">{STR.github.tokenExplain}</p>
      <ol className="hint token-steps">
        <li>
          <a href={backend.tokenCreateUrl} target="_blank" rel="noreferrer">
            {STR.github.createToken}
          </a>{' '}
          {STR.github.tokenStep1}
        </li>
        <li>{STR.github.tokenStep2}</li>
        <li>{STR.github.tokenStep3}</li>
      </ol>
      <p className={hasToken ? 'ok' : 'warning'}>
        {hasToken ? STR.github.tokenSet : STR.github.tokenMissing}
      </p>
      <div className="token-row">
        <input
          type="password"
          dir="ltr"
          placeholder="github_pat_..."
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button className="primary" onClick={save} disabled={!value.trim()}>
          {STR.github.saveToken}
        </button>
        {hasToken && (
          <button className="danger" onClick={clear}>
            {STR.github.clearToken}
          </button>
        )}
        <button className="secondary" onClick={onClose}>
          {STR.github.close}
        </button>
      </div>
      <p className="hint">{STR.github.tokenPersistNote}</p>
      {hasToken && (
        <div style={{ marginTop: 6 }}>
          <h4 style={{ margin: '8px 0 2px' }}>{STR.github.quickLinkTitle}</h4>
          <p className="hint" style={{ margin: '0 0 6px' }}>{STR.github.quickLinkExplain}</p>
          <button className="secondary" onClick={copyQuickLink}>
            {STR.github.quickLinkBtn}
          </button>
          {linkNote && <span className="ok" style={{ marginInlineStart: 8 }}>{linkNote}</span>}
        </div>
      )}

      <hr className="panel-divider" />
      <h3>{STR.captions.settingsTitle}</h3>
      <p className="hint">{STR.captions.keyExplain}</p>
      <p className={hasKey ? 'ok' : 'warning'}>
        {hasKey ? STR.captions.keySet : STR.captions.keyMissing}
      </p>
      <div className="token-row">
        <input
          type="password"
          dir="ltr"
          placeholder="sk-ant-..."
          value={keyValue}
          onChange={(e) => setKeyValue(e.target.value)}
        />
        <button className="primary" onClick={saveKey} disabled={!keyValue.trim()}>
          {STR.github.saveToken}
        </button>
        {hasKey && (
          <button className="danger" onClick={clearKey}>
            {STR.github.clearToken}
          </button>
        )}
      </div>
    </div>
  )
}
