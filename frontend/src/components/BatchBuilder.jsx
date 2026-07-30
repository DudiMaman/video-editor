import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { get, postForm } from '../api.js'
import RequestRow from './RequestRow.jsx'

let nextKey = 1
const emptyRow = () => ({
  key: nextKey++,
  assetId: '',
  outroId: '',
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
    if (active) get('/api/assets').then(setAssets).catch((e) => setError(e.message))
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
      const fd = new FormData()
      const payload = rows.map((r, i) => ({
        asset_id: Number(r.assetId),
        outro_id: Number(r.outroId),
        cut_seconds: parseFloat(r.cutSeconds),
        source_type: r.sourceType,
        ...(r.sourceType === 'url'
          ? { source_url: r.url.trim() }
          : { file_key: `file_${i}` }),
      }))
      rows.forEach((r, i) => {
        if (r.sourceType === 'upload') fd.append(`file_${i}`, r.file)
      })
      fd.append('requests', JSON.stringify(payload))
      await postForm('/api/batches', fd)
      setRows([emptyRow()])
      setOk(STR.batch.sentOk)
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
