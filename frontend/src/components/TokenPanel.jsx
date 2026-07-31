import { useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

export default function TokenPanel({ onClose }) {
  const [value, setValue] = useState('')
  const [hasToken, setHasToken] = useState(backend.hasToken())

  const save = () => {
    if (!value.trim()) return
    backend.setToken(value)
    setValue('')
    setHasToken(true)
  }

  const clear = () => {
    backend.clearToken()
    setHasToken(false)
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
    </div>
  )
}
