import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import ShareBar from './ShareBar.jsx'

const S = STR.scout

function ReviewCard({ entry, onUpdated }) {
  const [urls, setUrls] = useState(undefined) // undefined=loading, null=missing
  const [caption, setCaption] = useState(entry.caption || '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    backend.scout.resolveOutput(entry.output_asset).then(setUrls).catch(() => setUrls(null))
  }, [entry.output_asset])

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

  const approve = () => setStatus({ status: 'approved' }, `Scout: approve ${entry.video_id}`)

  const reject = () => {
    const reason = prompt(S.rejectReason) ?? ''
    setStatus(
      { status: 'rejected', reject_reason: reason },
      `Scout: reject ${entry.video_id}`
    )
  }

  const markPublished = () =>
    setStatus(
      {
        status: 'published',
        published_to: [...new Set([...(entry.published_to || []), 'tiktok'])],
        published_at: new Date().toISOString(),
      },
      `Scout: mark published ${entry.video_id}`
    )

  const saveCaption = async () => {
    await setStatus({ caption }, `Scout: edit caption ${entry.video_id}`)
    setNote(S.captionSaved)
    setTimeout(() => setNote(''), 2000)
  }

  // Shape ShareBar/backend helpers expect (same fields as batch requests).
  const shareRequest = urls
    ? { caption, _videoUrl: urls.videoUrl, _assetApiUrl: urls.assetApiUrl }
    : null

  return (
    <div className={`card result-card status-${entry.status === 'approved' ? 'done' : 'queued'}`}>
      <div className="result-head">
        <strong dir="ltr">{entry.output_asset}</strong>
        <span className={`badge badge-${entry.status === 'approved' ? 'done' : 'queued'}`}>
          {S.statuses[entry.status] || entry.status}
        </span>
      </div>
      {entry.source_url && (
        <p className="muted source-url" dir="ltr">
          <a href={entry.source_url} target="_blank" rel="noreferrer">
            {S.sourceLink}
          </a>{' '}
          {entry.source_url}
        </p>
      )}
      {urls === undefined && <p className="muted">…</p>}
      {urls === null && <p className="warning">{S.videoUnavailable}</p>}
      {urls && <video controls preload="metadata" src={urls.videoUrl} />}
      <textarea
        dir="ltr"
        rows={6}
        value={caption}
        onChange={(e) => setCaption(e.target.value)}
      />
      <div className="result-actions">
        <button
          className="secondary small"
          onClick={saveCaption}
          disabled={busy || caption === (entry.caption || '')}
        >
          {S.saveCaption}
        </button>
        {note && <span className="ok">{note}</span>}
      </div>
      {shareRequest && <ShareBar request={shareRequest} />}
      <div className="result-actions review-actions">
        {entry.status === 'pending_review' && (
          <>
            <button className="primary" onClick={approve} disabled={busy}>
              {S.approve}
            </button>
            <button className="danger" onClick={reject} disabled={busy}>
              {S.reject}
            </button>
          </>
        )}
        {entry.status === 'approved' && (
          <button className="primary" onClick={markPublished} disabled={busy}>
            {S.markPublishedBtn}
          </button>
        )}
      </div>
    </div>
  )
}

export default function ReviewTab({ active }) {
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState('')

  const load = () =>
    backend.scout
      .readLedger()
      .then((l) =>
        setEntries(l.filter((e) => ['pending_review', 'approved'].includes(e.status)))
      )
      .catch((e) => setError(e.message))

  useEffect(() => {
    if (active) load()
  }, [active])

  if (error) return <p className="error">{STR.errors.generic}{error}</p>
  if (entries === null) return <p className="empty">…</p>

  return (
    <div>
      <p className="hint">{S.reviewExplain}</p>
      {entries.length === 0 ? (
        <p className="empty">{S.reviewEmpty}</p>
      ) : (
        entries.map((e) => <ReviewCard key={e.video_id} entry={e} onUpdated={load} />)
      )}
    </div>
  )
}
