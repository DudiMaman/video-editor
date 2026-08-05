import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import ShareBar from './ShareBar.jsx'

const S = STR.scout

function ReviewCard({ entry, appName, onUpdated }) {
  const [urls, setUrls] = useState(undefined) // undefined=loading, null=missing
  const [caption, setCaption] = useState(entry.caption || '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    await navigator.clipboard.writeText(caption)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

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
        <strong>{appName}</strong>
        <span className="muted" dir="ltr">{entry.output_asset}</span>
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
        <button className="secondary small" onClick={copy} disabled={!caption}>
          {copied ? STR.results.copied : STR.results.copyCaption}
        </button>
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

// Ledger entries carry `asset` directly; older/agent-written entries may
// only reference their inbox page, so fall back through source_page.
export function assetIdOf(entry, inboxById) {
  return entry.asset || inboxById[entry.source_page]?.asset || ''
}

export default function ReviewTab({ active }) {
  const [entries, setEntries] = useState(null)
  const [assets, setAssets] = useState([])
  const [inboxById, setInboxById] = useState({})
  const [appFilter, setAppFilter] = useState('')
  const [error, setError] = useState('')

  const load = () =>
    backend.scout
      .readLedger()
      .then((l) =>
        setEntries(l.filter((e) => ['pending_review', 'approved'].includes(e.status)))
      )
      .catch((e) => setError(e.message))

  useEffect(() => {
    if (!active) return
    load()
    backend.listAssets().then(setAssets).catch(() => {})
    backend.scout
      .readInbox()
      .then((pages) =>
        setInboxById(Object.fromEntries(pages.map((p) => [p.id, p])))
      )
      .catch(() => {})
  }, [active])

  if (error) return <p className="error">{STR.errors.generic}{error}</p>
  if (entries === null) return <p className="empty">…</p>

  const appName = (id) =>
    assets.find((a) => String(a.id) === String(id))?.name || id || S.unknownAsset

  const idsInQueue = [...new Set(entries.map((e) => assetIdOf(e, inboxById)))]
  const shown = appFilter
    ? entries.filter((e) => String(assetIdOf(e, inboxById)) === appFilter)
    : entries

  return (
    <div>
      <p className="hint">{S.reviewExplain}</p>
      {idsInQueue.length > 1 && (
        <div className="log-filter">
          <button
            className={'tab' + (appFilter === '' ? ' active' : '')}
            onClick={() => setAppFilter('')}
          >
            {S.allApps} ({entries.length})
          </button>
          {idsInQueue.map((id) => (
            <button
              key={id || 'none'}
              className={'tab' + (appFilter === String(id) ? ' active' : '')}
              onClick={() => setAppFilter(String(id))}
            >
              {appName(id)} (
              {entries.filter((e) => String(assetIdOf(e, inboxById)) === String(id)).length})
            </button>
          ))}
        </div>
      )}
      {entries.length === 0 ? (
        <p className="empty">{S.reviewEmpty}</p>
      ) : (
        shown.map((e) => (
          <ReviewCard
            key={e.video_id}
            entry={e}
            appName={appName(assetIdOf(e, inboxById))}
            onUpdated={load}
          />
        ))
      )}
    </div>
  )
}
