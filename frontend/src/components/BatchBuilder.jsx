import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import RequestRow from './RequestRow.jsx'

let nextKey = 1
const emptyRow = () => ({
  key: nextKey++,
  assetId: '',
  outroId: '',
  introId: '',
  sourceType: 'upload',
  file: null,
  url: '',
  cutSeconds: '',
})

export default function BatchBuilder({ active, onSent }) {
  const [assets, setAssets] = useState([])
  const [rows, setRows] = useState([emptyRow()])
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  useEffect(() => {
    if (active) backend.listAssets().then(setAssets).catch((e) => setError(e.message))
  }, [active])

  const updateRow = (key, patch) => {
    setRows((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  }

  const validate = () => {
    for (const r of rows) {
      if (!r.assetId) return STR.batch.errMissingAsset
      if (!r.outroId) return STR.batch.errMissingOutro
      if (r.sourceType === 'upload' ? !r.file : !r.url.trim())
        return STR.batch.errMissingSource
      if (!(parseFloat(r.cutSeconds) > 0)) return STR.batch.errMissingCut
    }
    return ''
  }

  const send = async () => {
    setOk('')
    const problem = validate()
    if (problem) {
      setError(problem)
      return
    }
    setError('')
    setSending(true)
    try {
      await backend.submitBatch(rows)
      setRows([emptyRow()])
      setOk(backend.mode === 'github' ? STR.batch.sentOkGithub : STR.batch.sentOk)
      onSent()
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  if (assets.length === 0) {
    return <p className="empty">{STR.batch.noAssets}</p>
  }

  return (
    <div className="batch-builder">
      {rows.map((row) => (
        <RequestRow
          key={row.key}
          row={row}
          assets={assets}
          onChange={(patch) => updateRow(row.key, patch)}
          onAssetsChanged={() => backend.listAssets().then(setAssets)}
          onRemove={
            rows.length > 1
              ? () => setRows((rows) => rows.filter((r) => r.key !== row.key))
              : null
          }
        />
      ))}
      <div className="batch-actions">
        <button className="secondary" onClick={() => setRows((r) => [...r, emptyRow()])}>
          {STR.batch.addRow}
        </button>
        <button className="primary" onClick={send} disabled={sending}>
          {sending ? STR.batch.sending : STR.batch.send}
        </button>
      </div>
      {error && <p className="error">{STR.errors.generic}{error}</p>}
      {ok && <p className="ok">{ok}</p>}
    </div>
  )
}
