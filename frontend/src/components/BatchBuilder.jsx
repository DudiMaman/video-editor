import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import RequestRow from './RequestRow.jsx'
import ClipPicker from './ClipPicker.jsx'
import CleanPlayer from './CleanPlayer.jsx'

const S = STR.scout
const B = STR.batch

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
  subtitlesText: '',
})

// A curated video the user approved: a self-contained editor. All editing
// controls sit above the embedded source player, inside one card, so it is
// always clear which video is being edited. "Process" runs it through the
// pipeline (trim + subtitles + outro + caption); the pipeline wires the
// finished file back to the ledger as approved.
function EditingCard({ entry, assets, onUpdated, onAssetsChanged }) {
  const asset =
    assets.find((a) => String(a.id) === String(entry.asset)) || assets[0]
  const outros = asset ? asset.outros : []
  const intros = (asset && asset.intros) || []

  const [outroId, setOutroId] = useState(
    outros.length === 1 ? String(outros[0].id) : ''
  )
  const [introId, setIntroId] = useState('')
  const [startSeconds, setStartSeconds] = useState('')
  const [cutSeconds, setCutSeconds] = useState('')
  const [caption, setCaption] = useState(entry.caption || '')
  const [subtitlesText, setSubtitlesText] = useState('')
  const [transcriptKey, setTranscriptKey] = useState(null)
  const [openPicker, setOpenPicker] = useState(null) // 'outro' | 'intro' | null
  const [busy, setBusy] = useState(false)
  const [subBusy, setSubBusy] = useState(false)
  const [note, setNote] = useState('')
  const [subNote, setSubNote] = useState('')

  const suggested = entry.caption || ''
  const flash = (setter, msg, ms = 3000) => {
    setter(msg)
    setTimeout(() => setter(''), ms)
  }

  const clipName = (clips, id) =>
    clips.find((c) => String(c.id) === String(id))?.original_name

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

  const loadOriginalCaption = async () => {
    flash(setNote, '…', 1000)
    try {
      const r = await fetch(
        `https://www.tiktok.com/oembed?url=${encodeURIComponent(entry.source_url)}`
      )
      const j = await r.json()
      if (!j.title) throw new Error('no title')
      setCaption(j.title)
      flash(setNote, S.originalLoaded)
    } catch {
      flash(setNote, S.originalFailed)
      setCaption(suggested)
    }
  }

  const transcribe = async () => {
    setSubBusy(true)
    try {
      const key = await backend.submitTranscribe({
        sourceType: 'url',
        url: entry.source_url,
      })
      setTranscriptKey(key)
      flash(setSubNote, B.transcribeSent, 8000)
    } catch (e) {
      alert(e.message)
    } finally {
      setSubBusy(false)
    }
  }

  const loadTranscript = async () => {
    setSubBusy(true)
    try {
      const t = await backend.readTranscript(transcriptKey || entry.source_url)
      if (!t || !t.cues?.length) {
        flash(setSubNote, B.transcriptMissing)
      } else {
        setSubtitlesText(
          t.cues.map((c) => `${c.start} - ${c.end} | ${c.text}`).join('\n')
        )
        flash(setSubNote, B.transcriptLoaded)
      }
    } catch (e) {
      alert(e.message)
    } finally {
      setSubBusy(false)
    }
  }

  const uploadClip = async (kind, file) => {
    if (kind === 'intro') await backend.uploadIntro(asset.id, file)
    else await backend.uploadOutro(asset.id, file)
    await onAssetsChanged()
  }

  const process = async () => {
    if (!outroId) {
      alert(B.errMissingOutro)
      return
    }
    setBusy(true)
    try {
      await backend.submitEdit({
        assetId: asset.id,
        outroId,
        introId,
        startSeconds,
        cutSeconds,
        subtitlesText,
        caption,
        sourceUrl: entry.source_url,
        ledgerVideoId: entry.video_id,
      })
      // Mark that processing started; the pipeline flips it to approved
      // once the file is built and wired back.
      await setStatus(
        { caption, status: 'processing' },
        `Scout: process ${entry.video_id}`
      )
    } catch (e) {
      alert(e.message)
      setBusy(false)
    }
  }

  const reject = () => {
    const reason = prompt(S.rejectReason) ?? ''
    setStatus(
      { status: 'rejected', reject_reason: reason },
      `Scout: reject ${entry.video_id}`
    )
  }

  const srcDisabled = false

  return (
    <div className="card result-card status-queued editor-card">
      <div className="result-head">
        <strong>{S.statuses.editing}</strong>
        <span className="muted" dir="ltr">{entry.video_id}</span>
      </div>

      {/* --- all editing controls, above the video --- */}
      <div className="editor-controls">
        <div className="picker-field">
          <span className="picker-label">{B.outro}</span>
          <button
            className={'picker-trigger' + (openPicker === 'outro' ? ' active' : '')}
            onClick={() => setOpenPicker((p) => (p === 'outro' ? null : 'outro'))}
          >
            {clipName(outros, outroId) || B.selectOutro} ▾
          </button>
        </div>
        {openPicker === 'outro' && (
          <ClipPicker
            title={B.pickOutroTitle}
            clips={outros}
            value={outroId}
            urlFor={(c) => backend.outroUrl(c)}
            onPick={(id) => { setOutroId(id); setOpenPicker(null) }}
            onUpload={(file) => uploadClip('outro', file)}
          />
        )}

        {backend.supportsIntros && (
          <>
            <div className="picker-field">
              <span className="picker-label">{B.intro}</span>
              <button
                className={'picker-trigger' + (openPicker === 'intro' ? ' active' : '')}
                onClick={() => setOpenPicker((p) => (p === 'intro' ? null : 'intro'))}
              >
                {clipName(intros, introId) || B.noIntro} ▾
              </button>
            </div>
            {openPicker === 'intro' && (
              <ClipPicker
                title={B.pickIntroTitle}
                clips={intros}
                value={introId || ''}
                allowNone
                noneLabel={B.noIntro}
                urlFor={(c) => backend.introUrl(c)}
                onPick={(id) => { setIntroId(id); setOpenPicker(null) }}
                onUpload={(file) => uploadClip('intro', file)}
              />
            )}
          </>
        )}

        <div className="trim-fields">
          <label>
            {B.startSeconds}
            <input
              type="number" dir="ltr" min="0" step="0.1" placeholder="0"
              value={startSeconds}
              onChange={(e) => setStartSeconds(e.target.value)}
            />
          </label>
          <label>
            {B.cutSeconds}
            <input
              type="number" dir="ltr" min="0.1" step="0.1"
              value={cutSeconds}
              onChange={(e) => setCutSeconds(e.target.value)}
            />
          </label>
        </div>
        <p className="hint">{B.cutHint}</p>

        <label className="muted">{S.captionBoxLabel}</label>
        <textarea
          dir="ltr" rows={6} value={caption}
          onChange={(e) => setCaption(e.target.value)}
        />
        <div className="result-actions">
          <button className="secondary small" onClick={loadOriginalCaption} disabled={busy}>
            {S.loadOriginal}
          </button>
          <button
            className="secondary small"
            onClick={() => setCaption(suggested)}
            disabled={busy || caption === suggested}
          >
            {S.loadSuggested}
          </button>
          {note && <span className="ok">{note}</span>}
        </div>

        <div className="subtitles-field">
          <span className="picker-label">{B.subtitlesLabel}</span>
          <div className="result-actions">
            <button className="secondary small" onClick={transcribe} disabled={subBusy}>
              {B.transcribeBtn}
            </button>
            <button className="secondary small" onClick={loadTranscript} disabled={subBusy}>
              {B.loadTranscriptBtn}
            </button>
            {subNote && <span className="ok">{subNote}</span>}
          </div>
          <textarea
            dir="ltr" rows={6} placeholder={B.subtitlesPlaceholder}
            value={subtitlesText}
            onChange={(e) => setSubtitlesText(e.target.value)}
          />
          <p className="hint">{B.subtitlesHint}</p>
        </div>
      </div>

      {/* --- the video being edited --- */}
      <label className="muted">{S.sourcePreview}</label>
      <CleanPlayer entry={entry} />

      <div className="result-actions review-actions">
        <button className="primary" onClick={process} disabled={busy || srcDisabled}>
          {busy ? B.sending : S.processAndApprove}
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
      .then((l) =>
        setEditing(
          l.filter((e) => e.status === 'editing' || e.status === 'processing')
        )
      )
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
      if (!r.assetId) return B.errMissingAsset
      if (!r.outroId) return B.errMissingOutro
      if (r.sourceType === 'upload' ? !r.file : !r.url.trim())
        return B.errMissingSource
      const hasCut = String(r.cutSeconds).trim() !== ''
      const cut = parseFloat(r.cutSeconds)
      if (hasCut && !(cut > 0)) return B.errBadCut
      const start = parseFloat(r.startSeconds)
      if (hasCut && start && start >= cut) return B.errBadRange
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
      setOk(backend.mode === 'github' ? B.sentOkGithub : B.sentOk)
      onSent()
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(false)
    }
  }

  if (assets.length === 0) {
    return <p className="empty">{B.noAssets}</p>
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
            editing.map((e) =>
              e.status === 'processing' ? (
                <div key={e.video_id} className="card result-card status-queued">
                  <div className="result-head">
                    <strong>{S.statuses.processing}</strong>
                    <span className="muted" dir="ltr">{e.video_id}</span>
                  </div>
                  <p className="muted">{S.processingNote}</p>
                </div>
              ) : (
                <EditingCard
                  key={e.video_id}
                  entry={e}
                  assets={assets}
                  onUpdated={loadEditing}
                  onAssetsChanged={() => backend.listAssets().then(setAssets)}
                />
              )
            )}
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
              {B.addRow}
            </button>
            <button className="primary" onClick={send} disabled={sending}>
              {sending ? B.sending : B.send}
            </button>
          </div>
        </>
      )}
      {error && <p className="error">{STR.errors.generic}{error}</p>}
      {ok && <p className="ok">{ok}</p>}
    </div>
  )
}
