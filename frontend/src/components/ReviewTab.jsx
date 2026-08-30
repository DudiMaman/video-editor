import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import ShareBar from './ShareBar.jsx'
import CleanPlayer from './CleanPlayer.jsx'
import ScheduleCalendar from './ScheduleCalendar.jsx'

const S = STR.scout

// 19.8.2026 rendering for the scheduled-for / published-on indicators.
const fmtDay = (v) => {
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/)
  return m ? `${+m[3]}.${+m[2]}.${m[1]}` : String(v)
}

// stage: 'pending' | 'approved' | 'published'
const STAGE = {
  pending: { statuses: ['pending_review'], explain: () => S.reviewExplain, empty: () => S.reviewEmpty },
  approved: { statuses: ['approved'], explain: () => S.approvedExplain, empty: () => S.approvedEmpty },
  published: { statuses: ['published'], explain: () => S.publishedExplain, empty: () => S.publishedEmpty },
}

function ReviewCard({ entry, stage, appName, asset, siblings, onUpdated }) {
  const [urls, setUrls] = useState(undefined) // undefined=loading, null=missing
  const [caption, setCaption] = useState(entry.caption || '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [copied, setCopied] = useState(false)
  const [calOpen, setCalOpen] = useState(false)

  // Curation-only entries carry no processed output — just the source link.
  const linkOnly = !entry.output_asset

  useEffect(() => {
    if (linkOnly) return
    backend.scout.resolveOutput(entry.output_asset).then(setUrls).catch(() => setUrls(null))
  }, [entry.output_asset, linkOnly])

  const copy = async () => {
    await navigator.clipboard.writeText(caption)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
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

  // Curated (link-only) videos go through the editing stage in the batch
  // tab before landing in "approved"; processed videos skip straight there.
  const approve = () =>
    linkOnly
      ? setStatus({ status: 'editing' }, `Scout: to editing ${entry.video_id}`)
      : setStatus({ status: 'approved' }, `Scout: approve ${entry.video_id}`)

  const reject = () => {
    const reason = prompt(S.rejectReason) ?? ''
    setStatus(
      { status: 'rejected', reject_reason: reason },
      `Scout: reject ${entry.video_id}`
    )
  }

  const markUploaded = () => {
    if (!confirm(S.confirmUploaded)) return
    setStatus(
      {
        status: 'published',
        published_to: [...new Set([...(entry.published_to || []), 'social'])],
        published_at: new Date().toISOString(),
      },
      `Scout: uploaded ${entry.video_id}`
    )
  }

  const saveCaption = async () => {
    await setStatus({ caption }, `Scout: edit caption ${entry.video_id}`)
    setNote(S.captionSaved)
    setTimeout(() => setNote(''), 2000)
  }

  // ---- Zernio distribution (server-side; the tab only marks intent) ----

  // Ready once the venture has a Zernio publish target (multi-platform
  // zernioTargets, or the legacy single zernioAccountId) and this entry
  // has a processed output to send. Mirrors targets_of() in the runner.
  const hasZernioTarget =
    (Array.isArray(asset?.zernioTargets) &&
      asset.zernioTargets.some((t) => t?.platform && t?.accountId)) ||
    !!asset?.zernioAccountId
  const zernioReady = hasZernioTarget && !!entry.output_asset

  const confirmSchedule = async (date, time) => {
    await setStatus({ schedule: { date, time } },
      `Scout: schedule ${entry.video_id} for ${date} ${time}`)
    setCalOpen(false)
    setNote(S.scheduledOk)
    setTimeout(() => setNote(''), 3000)
  }

  const publishNow = async () => {
    if (!confirm(S.publishNowConfirm)) return
    await setStatus({ publishNow: true }, `Scout: publish now ${entry.video_id}`)
    setNote(S.publishNowQueued)
    setTimeout(() => setNote(''), 3000)
  }

  // Days already taken by this app's other scheduled/published videos -
  // one post per app per day, same rule as the characters' calendar.
  const occupied = {}
  for (const s of siblings || []) {
    if (s.video_id === entry.video_id) continue
    const d = (s.schedule || {}).date ||
      (s.status === 'published' ? String(s.published_at || '').slice(0, 10) : '')
    if (!d) continue
    occupied[d] = {
      img: null,
      tag: s.status === 'published' ? S.publishedTag : S.scheduledTag,
      tagColor: s.status === 'published' ? '#0891b2' : '#22a06b',
    }
  }
  const upcoming = Object.keys(occupied)
    .filter((d) => d >= new Date().toISOString().slice(0, 10))
    .sort()[0]

  // Shape ShareBar/backend helpers expect (same fields as batch requests).
  const shareRequest = urls
    ? { caption, _videoUrl: urls.videoUrl, _assetApiUrl: urls.assetApiUrl }
    : null

  return (
    <div className={`card result-card status-${stage === 'pending' ? 'queued' : 'done'}`}>
      <div className="result-head">
        <strong>{appName}</strong>
        <span className="muted" dir="ltr">{entry.output_asset}</span>
        {stage === 'published' && entry.published_at && (
          <span className="badge badge-done">
            {S.publishedTag} {fmtDay(entry.published_at)}
            {entry.zernioPostUrl && (
              <>
                {' · '}
                <a href={entry.zernioPostUrl} target="_blank" rel="noreferrer">{S.viewPost}</a>
              </>
            )}
          </span>
        )}
      </div>
      {stage === 'approved' && entry.schedule?.date && (
        <p style={{ color: '#22a06b', fontWeight: 600, fontSize: 13, margin: '2px 0' }}>
          {S.scheduledOn} {fmtDay(entry.schedule.date)} {S.atHour} {entry.schedule.time}
          {entry.zernioPostId && <span style={{ marginInlineStart: 8 }}>{S.distScheduled}</span>}
        </p>
      )}
      {stage === 'approved' && entry.publishNow && !entry.zernioPostId && (
        <p style={{ color: '#b8860b', fontWeight: 600, fontSize: 13, margin: '2px 0' }}>{S.publishingNow}</p>
      )}
      {entry.distribution?.state === 'failed' && (
        <p className="error" style={{ fontSize: 12, margin: '2px 0' }} title={entry.distribution?.error || ''}>
          {S.distFailed}{entry.distribution?.error ? `: ${String(entry.distribution.error).slice(0, 80)}` : ''}
        </p>
      )}
      {entry.source_url && (
        <p className="muted source-url" dir="ltr">
          <a href={entry.source_url} target="_blank" rel="noreferrer">
            {S.sourceLink}
          </a>{' '}
          {entry.source_url}
        </p>
      )}
      {linkOnly ? (
        <CleanPlayer entry={entry} />
      ) : (
        <>
          {urls === undefined && <p className="muted">…</p>}
          {urls === null && <p className="warning">{S.videoUnavailable}</p>}
          {urls && <video controls preload="metadata" src={urls.videoUrl} />}
        </>
      )}
      <textarea
        dir="ltr"
        rows={6}
        value={caption}
        readOnly={stage === 'published'}
        onChange={(e) => setCaption(e.target.value)}
      />
      <div className="result-actions">
        <button className="secondary small" onClick={copy} disabled={!caption}>
          {copied ? STR.results.copied : STR.results.copyCaption}
        </button>
        {stage !== 'published' && (
          <button
            className="secondary small"
            onClick={saveCaption}
            disabled={busy || caption === (entry.caption || '')}
          >
            {S.saveCaption}
          </button>
        )}
        {note && <span className="ok">{note}</span>}
      </div>
      {/* The manual device-share row belongs to the pre-Zernio flow. In the
          approved stage the real publish paths are the Zernio buttons
          below (schedule / publish now), so the manual bar is shown only
          in the pending review stage to avoid two competing "share" UIs. */}
      {stage === 'pending' && shareRequest && <ShareBar request={shareRequest} />}
      <div className="result-actions review-actions">
        {stage === 'pending' && (
          <>
            <button className="primary" onClick={approve} disabled={busy}>
              {S.approve}
            </button>
            <button className="danger" onClick={reject} disabled={busy}>
              {S.reject}
            </button>
          </>
        )}
        {stage === 'approved' && (
          <>
            <button
              className="primary"
              onClick={() => setCalOpen(true)}
              disabled={busy || !zernioReady || !!entry.zernioPostId}
              title={!zernioReady ? S.zernioHintApps : ''}
            >
              {S.scheduleBtn}
            </button>
            <button
              onClick={publishNow}
              disabled={busy || !zernioReady || !!entry.zernioPostId || !!entry.publishNow}
              title={!zernioReady ? S.zernioHintApps : ''}
              style={{ background: '#22a06b', color: '#fff', border: 'none', borderRadius: 6, padding: '8px 14px', fontWeight: 700, cursor: 'pointer', opacity: (!zernioReady || entry.zernioPostId || entry.publishNow) ? 0.5 : 1 }}
            >
              {S.publishNowBtn}
            </button>
            <button className="secondary" onClick={markUploaded} disabled={busy}>
              {S.markUploadedBtn}
            </button>
          </>
        )}
        {stage === 'approved' && !zernioReady && (
          <span className="muted" style={{ fontSize: 11 }}>{S.zernioHintApps}</span>
        )}
      </div>
      {calOpen && (
        <ScheduleCalendar
          header={(
            <div>
              <b>{S.schedCalendarOf} {appName}</b>
              <div className="muted" style={{ fontSize: 12 }}>{S.schedOnlyThisApp}</div>
            </div>
          )}
          occupied={occupied}
          initialDate={entry.schedule?.date || upcoming}
          defaultTime={entry.schedule?.time || '18:00'}
          months={STR.aimodels.months}
          legend={[
            { color: '#22a06b', label: S.scheduledTag },
            { color: '#0891b2', label: S.publishedTag },
          ]}
          confirmLabel={(day, m1, time) =>
            busy ? '…' : day ? `${S.scheduleTo}${day}.${m1} ${S.atHour} ${time}` : `${S.scheduleTo}—`}
          onConfirm={confirmSchedule}
          onClose={() => setCalOpen(false)}
          busy={busy}
        />
      )}
    </div>
  )
}

// Ledger entries carry `asset` directly; older/agent-written entries may
// only reference their inbox page, so fall back through source_page.
export function assetIdOf(entry, inboxById) {
  return entry.asset || inboxById[entry.source_page]?.asset || ''
}

export default function ReviewTab({ active, stage = 'pending' }) {
  const cfg = STAGE[stage]
  const [entries, setEntries] = useState(null)
  const [assets, setAssets] = useState([])
  const [inboxById, setInboxById] = useState({})
  const [appFilter, setAppFilter] = useState('')
  const [error, setError] = useState('')

  const [allEntries, setAllEntries] = useState([])

  const load = () =>
    backend.scout
      .readLedger()
      .then((l) => {
        setAllEntries(l)
        setEntries(l.filter((e) => cfg.statuses.includes(e.status)))
      })
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

  const idsHere = [...new Set(entries.map((e) => assetIdOf(e, inboxById)))]
  const shown = appFilter
    ? entries.filter((e) => String(assetIdOf(e, inboxById)) === appFilter)
    : entries

  return (
    <div>
      <p className="hint">{cfg.explain()}</p>
      {idsHere.length > 1 && (
        <div className="log-filter">
          <button
            className={'tab' + (appFilter === '' ? ' active' : '')}
            onClick={() => setAppFilter('')}
          >
            {S.allApps} ({entries.length})
          </button>
          {idsHere.map((id) => (
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
        <p className="empty">{cfg.empty()}</p>
      ) : (
        shown.map((e) => {
          const aid = assetIdOf(e, inboxById)
          return (
            <ReviewCard
              key={e.video_id}
              entry={e}
              stage={stage}
              appName={appName(aid)}
              asset={assets.find((a) => String(a.id) === String(aid))}
              siblings={allEntries.filter(
                (s) => String(assetIdOf(s, inboxById)) === String(aid))}
              onUpdated={load}
            />
          )
        })
      )}
    </div>
  )
}
