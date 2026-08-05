import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const S = STR.scout

export default function BriefTab({ active }) {
  const [text, setText] = useState(null)
  const [sha, setSha] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  useEffect(() => {
    if (!active || text !== null) return
    backend.scout
      .readBrief()
      .then((r) => {
        setText(r.text)
        setSha(r.sha)
      })
      .catch((e) => setError(e.message))
  }, [active])

  const save = async () => {
    setOk('')
    setError('')
    setBusy(true)
    try {
      await backend.scout.writeBrief(text, sha)
      // re-read to pick up the new sha for the next save
      const r = await backend.scout.readBrief()
      setSha(r.sha)
      setOk(S.briefSaved)
    } catch (e) {
      setError(e.status === 409 ? S.briefConflict : e.message)
    } finally {
      setBusy(false)
    }
  }

  if (text === null) return <p className="empty">…</p>

  return (
    <div className="card">
      <h3>{S.briefTitle}</h3>
      <p className="hint">{S.briefExplain}</p>
      <textarea
        className="brief-editor"
        rows={24}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setOk('')
        }}
      />
      <div className="batch-actions">
        <button className="primary" onClick={save} disabled={busy}>
          {S.briefSave}
        </button>
      </div>
      {error && <p className="error">{STR.errors.generic}{error}</p>}
      {ok && <p className="ok">{ok}</p>}
    </div>
  )
}
