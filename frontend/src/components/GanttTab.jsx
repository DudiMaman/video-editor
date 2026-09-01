import { useEffect, useRef, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const G = STR.gantt
const pad2 = (n) => String(n).padStart(2, '0')

const PLATFORM_SHORT = {
  instagram: 'IG', tiktok: 'TT', facebook: 'FB', youtube: 'YT',
  twitter: 'X', threads: 'TH', linkedin: 'LI', pinterest: 'PIN',
}

// The calendar day an entry lives on: where it actually published, else
// where it is scheduled to.
const dayOf = (e) => {
  if (e.status === 'published' && e.published_at) {
    return String(e.published_at).slice(0, 10)
  }
  return (e.schedule || {}).date || ''
}

// published (live) / problem (failed) / waiting (queue/media hold or
// in-flight) / scheduled (sent or about to be sent on time)
const stateOf = (e) => {
  const d = e.distribution || {}
  if (e.status === 'published') return 'published'
  if (d.state === 'failed') return 'problem'
  if (d.state === 'queue_wait' || d.state === 'media_wait' ||
      d.state === 'publishing' || e.publishNow) return 'waiting'
  return 'scheduled'
}

const STATE_STYLE = {
  scheduled: { background: '#22a06b', color: '#fff' },
  published: { background: '#0891b2', color: '#fff' },
  waiting: { background: '#b8860b', color: '#fff' },
  problem: { background: '#d33', color: '#fff' },
}

const platformsShort = (e) => {
  const ps = e.platforms && e.platforms.length
    ? e.platforms
    : ((e.distribution || {}).platforms || [])
  return ps.map((p) => PLATFORM_SHORT[p] || p).join('·')
}

// Read-only monthly Gantt per venture: what is scheduled ahead, what
// already published, what is stuck - with venture chips and month
// navigation. Data comes straight from the ledger; a schedule/publish
// anywhere in the app re-renders this tab live (ve-ledger-changed).
export default function GanttTab({ active }) {
  const now = new Date()
  const [entries, setEntries] = useState(null)
  const [assets, setAssets] = useState([])
  const [venture, setVenture] = useState('')
  const [month, setMonth] = useState({ y: now.getFullYear(), m0: now.getMonth() })
  const [error, setError] = useState('')

  const load = () =>
    backend.scout.readLedger().then(setEntries).catch((e) => setError(e.message))

  useEffect(() => {
    if (!active) return
    load()
    backend.listAssets().then(setAssets).catch(() => {})
  }, [active])

  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    const onChanged = () => loadRef.current()
    window.addEventListener('ve-ledger-changed', onChanged)
    return () => window.removeEventListener('ve-ledger-changed', onChanged)
  }, [])

  if (error) return <p className="error">{STR.errors.generic}{error}</p>
  if (entries === null) return <p className="empty">…</p>

  // Ventures = every asset; ones with calendar content first.
  const withDay = entries.filter((e) => dayOf(e))
  const ventureIds = [...new Set([
    ...assets.map((a) => String(a.id)),
    ...withDay.map((e) => String(e.asset || '')),
  ])].filter(Boolean)
  const hasContent = (id) => withDay.some((e) => String(e.asset) === id)
  ventureIds.sort((a, b) => (hasContent(b) ? 1 : 0) - (hasContent(a) ? 1 : 0))
  const current = venture || ventureIds.find(hasContent) || ventureIds[0] || ''
  const nameOf = (id) =>
    assets.find((a) => String(a.id) === id)?.name || id

  if (!ventureIds.length) return <p className="empty">{G.noVentures}</p>

  const mine = entries.filter((e) => String(e.asset) === current)
  const byDay = {}
  for (const e of mine) {
    const d = dayOf(e)
    if (!d) continue
    ;(byDay[d] = byDay[d] || []).push(e)
  }
  for (const d in byDay) {
    byDay[d].sort((a, b) =>
      String((a.schedule || {}).time || '').localeCompare(
        String((b.schedule || {}).time || '')))
  }

  const awaiting = mine.filter((e) =>
    e.status === 'approved' && !(e.schedule || {}).date &&
    !e.publishNow && !e.zernioPostId).length

  const monthKey = `${month.y}-${pad2(month.m0 + 1)}`
  const inMonth = Object.entries(byDay)
    .filter(([d]) => d.startsWith(monthKey))
    .flatMap(([, list]) => list)
  const nSched = inMonth.filter((e) => stateOf(e) !== 'published').length
  const nPub = inMonth.filter((e) => stateOf(e) === 'published').length

  const moveMonth = (dir) => setMonth(({ y, m0 }) => {
    const m = m0 + dir
    return m < 0 ? { y: y - 1, m0: 11 } : m > 11 ? { y: y + 1, m0: 0 } : { y, m0: m }
  })
  const monthDays = new Date(month.y, month.m0 + 1, 0).getDate()
  const monthOffset = new Date(month.y, month.m0, 1).getDay()
  const todayKey = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`

  const MAX_SHOWN = 3

  return (
    <div>
      <h2>{G.title}</h2>
      <p className="hint">{G.explain}</p>

      <div className="log-filter">
        {ventureIds.map((id) => (
          <button
            key={id}
            className={'tab' + (current === id ? ' active' : '')}
            onClick={() => setVenture(id)}
          >
            {nameOf(id)}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16, margin: '12px 0 6px' }}>
        <button onClick={() => moveMonth(-1)} style={{ fontSize: 18, padding: '2px 12px' }}>‹</button>
        <b style={{ fontSize: 17 }}>{STR.aimodels.months[month.m0]} {month.y}</b>
        <button onClick={() => moveMonth(1)} style={{ fontSize: 18, padding: '2px 12px' }}>›</button>
      </div>

      <div style={{ display: 'flex', gap: 18, justifyContent: 'center', fontSize: 13, marginBottom: 10 }}>
        <span>{G.monthScheduled(nSched)}</span>
        <span>{G.monthPublished(nPub)}</span>
        <span style={awaiting ? { color: '#b8860b', fontWeight: 600 } : { opacity: 0.7 }}>
          {G.awaiting(awaiting)}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 6 }}>
        {['א', 'ב', 'ג', 'ד', 'ה', 'ו', 'ש'].map((d) => (
          <div key={d} style={{ fontSize: 12, opacity: 0.6, textAlign: 'center' }}>{d}</div>
        ))}
        {Array.from({ length: monthOffset }, (_, i) => <div key={`e${i}`} />)}
        {Array.from({ length: monthDays }, (_, i) => {
          const d = i + 1
          const key = `${month.y}-${pad2(month.m0 + 1)}-${pad2(d)}`
          const posts = byDay[key] || []
          const isToday = key === todayKey
          return (
            <div
              key={d}
              style={{
                minHeight: 84, border: isToday ? '2px solid #6ea8fe' : '1px solid var(--border,#ccc)',
                background: posts.length ? 'rgba(34,160,107,.06)' : 'transparent',
                borderRadius: 8, padding: '3px 4px', fontSize: 11,
                display: 'flex', flexDirection: 'column', gap: 3, overflow: 'hidden',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ opacity: 0.75 }}>{d}</span>
                {isToday && <span style={{ color: '#6ea8fe', fontWeight: 700, fontSize: 10 }}>{G.todayTag}</span>}
              </div>
              {posts.slice(0, MAX_SHOWN).map((e) => {
                const st = stateOf(e)
                const label = st === 'published' ? STR.scout.publishedTag
                  : st === 'problem' ? G.legendProblem
                  : st === 'waiting' ? G.legendWaiting
                  : ((e.schedule || {}).time || STR.scout.scheduledTag)
                return (
                  <span
                    key={e.video_id}
                    title={(e.caption || '').slice(0, 140)}
                    style={{
                      ...STATE_STYLE[st], borderRadius: 4, padding: '1px 4px',
                      fontSize: 10, whiteSpace: 'nowrap', overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {label}{platformsShort(e) ? ` · ${platformsShort(e)}` : ''}
                  </span>
                )
              })}
              {posts.length > MAX_SHOWN && (
                <span style={{ fontSize: 10, opacity: 0.7 }}>{G.moreCount(posts.length - MAX_SHOWN)}</span>
              )}
            </div>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: 16, justifyContent: 'center', fontSize: 12, opacity: 0.8, marginTop: 12 }}>
        {[['scheduled', G.legendScheduled], ['published', G.legendPublished],
          ['waiting', G.legendWaiting], ['problem', G.legendProblem]].map(([k, label]) => (
          <span key={k}>
            <i style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 3, background: STATE_STYLE[k].background, marginInlineStart: 4, verticalAlign: 'middle' }} /> {label}
          </span>
        ))}
      </div>

      {!Object.keys(byDay).length && (
        <p className="empty" style={{ marginTop: 14 }}>{G.empty}</p>
      )}
    </div>
  )
}
