import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import RequestRow from './RequestRow.jsx'

const S = STR.scout

let nextKey = 1
const emptyRow = () => ({
  key: nextKey++,
  assetId: '',
  outroId: '',
  introId: '',
  sourceType: 'url',
  file: null,
  url: '',
  startSeconds: '',
  cutSeconds: '',
})

// A curated video the user approved: embedded player + live caption box.
// "Finish editing" moves it on to the approved tab.
function EditingCard({ entry, onUpdated }) {
  const [caption, setCaption] = useState(entry.caption || '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  // The suggested caption written at scan time, kept for one-tap restore.
  const suggested = entry.caption || ''

  const loadOriginal = async () => {
    setNote('…')
    try {
      const r = await fetch(
        `https://www.tiktok.com/oembed?url=${encodeURIComponent(entry.source_url)}`
      )
      const j = await r.json()
      if (!j.title) throw new Error('no title')
      setCaption(j.title)
      setNote(S.originalLoaded)
    } catch {
      setNote(S.originalFailed)
      setCaption(suggested)
    }
    setTimeout(() => setNote(''), 2500)
  }

  const setStatus = async (patch, message) => {
    setBusy(true)
    try {
      await backend.scout.mutateLedger((list) => {
        const e = list.find((x) => x.video_id === entry.video_id)
        if (!e) return false
        Object.assign(e, patch)
      }, message)
      onUpdated()
    } catch (e) {
      alert(e.message)
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    await setStatus({ caption }, `Scout: edit caption ${entry.video_id}`)
    setNote(S.captionSaved)
    setTimeout(() => setNote(''), 2000)
  }

  const finish = () =>
    setStatus(
      { caption, status: 'approved' },
      `Scout: finish editing ${entry.video_id}`
    )

  const reject = () => {
    const reason = prompt(S.rejectReason) ?? ''
    setStatus(
      { status: 'rejected', reject_reason: reason },
      `Scout: reject ${entry.video_id}`
    )
  }

  return (
    <div className="card result-card status-queued">
      <div className="result-head">
        <strong>{S.statuses.editing}</strong>
        <span className="muted" dir="ltr">{entry.video_id}</span>
      </div>
      <iframe
        className="tiktok-embed-frame"
        src={`https://www.tiktok.com/embed/v2/${entry.video_id}`}
        title={entry.video_id}
        allow="encrypted-media; fullscreen"
        allowFullScreen
      />
      <label className="muted">{S.captionBoxLabel}</label>
      <textarea
        dir="ltr"
        rows={7}
        value={caption}
        onChange={(e) => setCaption(e.target.value)}
      />
      <div className="result-actions">
        <button className="secondary small" onClick={loadOriginal} disabled={busy}>
          {S.loadOriginal}
        </button>
        <button
          className="secondary small"
          onClick={() => setCaption(suggested)}
          disabled={busy || caption === suggested}
        >
          {S.loadSuggested}
        </button>
        <button
          className="secondary small"
          onClick={save}
          disabled={busy || caption === (entry.caption || '')}
        >
          {S.saveCaption}
        </button>
        {note && <span className="ok">{note}</span>}
      </div>
      <div className="result-actions review-actions">
        <button className="primary" onClick={finish} disabled={busy}>
          {S.finishEditing}
        </button>
        <button className="danger" onClick={reject} disabled={busy}>
          {S.reject}
        </button>
      </div>
    </div>
  )
}

export default function BatchBuilder({ active, onSent }) {
  const [assets, setAssets] = useState([])
  const [rows, setRows] = useState([emptyRow()])
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')
  const [editing, setEditing] = useState(null)
  const [showManual, setShowManual] = useState(!backend.supportsScout)

  const loadEditing = () =>
    backend.scout
      .readLedger()
      .then((l) => setEditing(l.filter((e) => e.status === 'editing')))
      .catch(() => setEditing([]))

  useEffect(() => {
    if (!active) return
    backend.listAssets().then(setAssets).catch((e) => setError(e.message))
    if (backend.supportsScout) loadEditing()
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
      const hasCut = String(r.cutSeconds).trim() !== ''
      const cut = parseFloat(r.cutSeconds)
      if (hasCut && !(cut > 0)) return STR.batch.errBadCut
      const start = parseFloat(r.startSeconds)
      if (hasCut && start && start >= cut) return STR.batch.errBadRange
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
      {backend.supportsScout && (
        <section className="editing-section">
          <p className="muted">{S.editingExplain}</p>
          {editing === null && <p className="empty">…</p>}
          {editing && editing.length === 0 && (
            <p className="empty">{S.editingEmpty}</p>
          )}
          {editing &&
            editing.map((e) => (
              <EditingCard key={e.video_id} entry={e} onUpdated={loadEditing} />
            ))}
        </section>
      )}
      {backend.supportsScout && (
        <button
          className="secondary manual-toggle"
          onClick={() => setShowManual((v) => !v)}
        >
          {showManual ? S.manualToggleHide : S.manualToggle}
        </button>
      )}
      {showManual && (
        <>
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
        </>
      )}
      {error && <p className="error">{STR.errors.generic}{error}</p>}
      {ok && <p className="ok">{ok}</p>}
    </div>
  )
}
