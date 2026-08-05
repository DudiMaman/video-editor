import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import { assetIdOf } from './ReviewTab.jsx'

const S = STR.scout

export default function LogTab({ active }) {
  const [entries, setEntries] = useState(null)
  const [assets, setAssets] = useState([])
  const [inboxById, setInboxById] = useState({})
  const [filter, setFilter] = useState('')
  const [appFilter, setAppFilter] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!active) return
    backend.scout.readLedger().then(setEntries).catch((e) => setError(e.message))
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
  if (entries.length === 0) return <p className="empty">{S.logEmpty}</p>

  const appName = (id) =>
    assets.find((a) => String(a.id) === String(id))?.name || id || S.unknownAsset

  // Last-run summary: status counts for the most recent scan date.
  const lastDate = entries.reduce(
    (m, e) => (e.found_at > m ? e.found_at : m),
    ''
  )
  const lastRun = entries.filter((e) => e.found_at === lastDate)
  const counts = {}
  for (const e of lastRun) counts[e.status] = (counts[e.status] || 0) + 1

  const idsInLog = [...new Set(entries.map((e) => assetIdOf(e, inboxById)))]
  const shown = entries
    .filter((e) => !filter || e.status === filter)
    .filter((e) => !appFilter || String(assetIdOf(e, inboxById)) === appFilter)

  return (
    <div>
      <div className="card">
        <h3>{S.lastRun} — {lastDate}</h3>
        <p className="muted">
          {Object.entries(counts)
            .map(([k, n]) => `${S.statuses[k] || k}: ${n}`)
            .join(' · ')}
        </p>
      </div>

      {idsInLog.length > 1 && (
        <div className="log-filter">
          <button
            className={'tab' + (appFilter === '' ? ' active' : '')}
            onClick={() => setAppFilter('')}
          >
            {S.allApps} ({entries.length})
          </button>
          {idsInLog.map((id) => (
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

      <div className="log-filter">
        <button
          className={'tab' + (filter === '' ? ' active' : '')}
          onClick={() => setFilter('')}
        >
          {S.filterAll} ({entries.length})
        </button>
        {Object.keys(S.statuses)
          .filter((k) => entries.some((e) => e.status === k))
          .map((k) => (
            <button
              key={k}
              className={'tab' + (filter === k ? ' active' : '')}
              onClick={() => setFilter(k)}
            >
              {S.statuses[k]} ({entries.filter((e) => e.status === k).length})
            </button>
          ))}
      </div>

      {shown.map((e) => (
        <div key={e.video_id} className="card log-entry">
          <div className="result-head">
            <strong>{appName(assetIdOf(e, inboxById))}</strong>
            <span className="muted">{e.found_at}</span>
            <span
              className={
                'badge badge-' +
                (e.status === 'published' || e.status === 'approved' || e.status === 'edited'
                  ? 'done'
                  : e.status === 'failed' || e.status === 'rejected'
                    ? 'failed'
                    : 'queued')
              }
            >
              {S.statuses[e.status] || e.status}
            </span>
          </div>
          <p className="muted source-url" dir="ltr">{e.source_url}</p>
          {e.reject_reason && <p className="hint">{e.reject_reason}</p>}
          {e.output_asset && <p className="muted" dir="ltr">→ {e.output_asset}</p>}
          {e.published_at && (
            <p className="muted" dir="ltr">
              {(e.published_to || []).join(', ')} · {e.published_at.slice(0, 10)}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
