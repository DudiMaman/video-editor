import { useEffect, useMemo, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const S = STR.aimodels

// Content flow for the AI models project: batch.json holds every content
// item (the tab is the project's single gateway). Each pending item is
// either scheduled onto its character's own monthly calendar (date+time,
// status "scheduled") or rejected. Scheduled posts export as a CSV for
// the social scheduler; actual publishing is a separate later stage.

function csvEscape(v) {
  const s = String(v ?? '')
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
}

// generatedAt is ISO ("2026-08-14" or "2026-08-14T09:12:00Z") - show it
// the readable way (14.8.2026, plus the time when one is recorded).
function fmtCreated(v) {
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}:\d{2}))?/)
  if (!m) return String(v)
  const d = `${+m[3]}.${+m[2]}.${m[1]}`
  return m[4] ? `${d} ${m[4]}` : d
}

// Legacy items marked "approved" (the pre-scheduling decision model) count
// as scheduled everywhere - display, filtering and export.
function statusOf(p) {
  return p.status === 'approved' ? 'scheduled' : p.status
}

const pad2 = (n) => String(n).padStart(2, '0')
const dateKey = (y, m0, d) => `${y}-${pad2(m0 + 1)}-${pad2(d)}`

export default function AiModelsTab({ active }) {
  const [view, setView] = useState('batch') // batch | roster
  const [roster, setRoster] = useState(null)
  const [batch, setBatch] = useState(null)
  const [plan, setPlan] = useState({})
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [charFilter, setCharFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  // Scheduling modal state: the item being placed on its character's
  // calendar, the month shown, and the picked day/time.
  const [sched, setSched] = useState(null)
  const [schedMonth, setSchedMonth] = useState(null) // {y, m0}
  const [schedDay, setSchedDay] = useState(null)
  const [schedTime, setSchedTime] = useState('19:00')
  const [schedErr, setSchedErr] = useState('')

  useEffect(() => {
    if (!active || roster !== null) return
    if (!backend.aimodels) {
      setError(S.githubOnly)
      return
    }
    Promise.all([
      backend.aimodels.readRoster(),
      backend.aimodels.readBatch(),
      backend.aimodels.readPlan ? backend.aimodels.readPlan() : Promise.resolve({}),
    ])
      .then(([r, b, pl]) => {
        setRoster(r)
        setBatch(b)
        setPlan(pl || {})
      })
      .catch((e) => setError(e.message))
  }, [active])

  const byId = useMemo(() => {
    const m = {}
    for (const c of roster || []) m[c.id] = c
    return m
  }, [roster])

  const counts = useMemo(() => {
    const c = { pending: 0, scheduled: 0, rejected: 0 }
    for (const p of batch?.posts || []) c[statusOf(p)] = (c[statusOf(p)] || 0) + 1
    return c
  }, [batch])

  // The gallery is grouped per character (roster order) with a clear
  // header between characters; filterable by character and by status.
  const groups = useMemo(() => {
    const posts = (batch?.posts || []).filter(
      (p) => (!charFilter || p.char === charFilter) &&
             (!statusFilter || statusOf(p) === statusFilter)
    )
    const m = new Map()
    for (const p of posts) {
      if (!m.has(p.char)) m.set(p.char, [])
      m.get(p.char).push(p)
    }
    const order = (roster || []).map((c) => c.id).filter((id) => m.has(id))
    for (const id of m.keys()) if (!order.includes(id)) order.push(id)
    return order.map((id) => [id, m.get(id).sort((a, b) => ((a.date || '') < (b.date || '') ? 1 : -1))])
  }, [batch, roster, charFilter, statusFilter])

  const setStatus = (id, status) => {
    setBatch((b) => ({
      ...b,
      posts: b.posts.map((p) => (p.id === id ? { ...p, status } : p)),
    }))
    setDirty(true)
    setOk('')
  }

  const save = async () => {
    setBusy(true)
    setError('')
    try {
      await backend.aimodels.writeBatch(batch)
      setDirty(false)
      setOk(S.saved)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Scheduled posts (legacy "approved" included) feed the scheduler CSV.
  const exportCsv = () => {
    const rows = [['account', 'date', 'time', 'timezone', 'text', 'image_url']]
    for (const p of batch.posts) {
      if (statusOf(p) !== 'scheduled') continue
      const c = byId[p.char] || {}
      rows.push([c.handle || p.char, p.date, p.time, c.tz || '', p.caption, p.image])
    }
    const csv = '﻿' + rows.map((r) => r.map(csvEscape).join(',')).join('\r\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    a.download = `aimodels-${batch.week || 'batch'}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  const doImport = async () => {
    setError('')
    try {
      const parsed = JSON.parse(importText)
      if (!Array.isArray(parsed.posts)) throw new Error(S.importInvalid)
      setBatch(parsed)
      setDirty(true)
      setImportOpen(false)
      setImportText('')
      setOk(S.imported)
    } catch (e) {
      setError(e.message)
    }
  }

  // ---- scheduling modal ----

  const openSchedule = (p) => {
    const now = new Date()
    setSched(p)
    setSchedMonth({ y: now.getFullYear(), m0: now.getMonth() })
    setSchedDay(null)
    setSchedTime(
      (plan.post_times_by_char || {})[p.char] || p.time || '19:00')
    setSchedErr('')
  }

  // Days of the shown month already taken by this character's OTHER
  // scheduled posts - a character never posts twice on one day.
  const occupied = useMemo(() => {
    if (!sched || !schedMonth) return {}
    const m = {}
    for (const p of batch?.posts || []) {
      if (p.char !== sched.char || p.id === sched.id) continue
      if (statusOf(p) !== 'scheduled' || !p.date) continue
      const [y, mm, dd] = String(p.date).split('-').map(Number)
      if (y === schedMonth.y && mm === schedMonth.m0 + 1) m[dd] = p
    }
    return m
  }, [sched, schedMonth, batch])

  const confirmSchedule = async () => {
    if (!sched || !schedDay) return
    const date = dateKey(schedMonth.y, schedMonth.m0, schedDay)
    const next = {
      ...batch,
      posts: batch.posts.map((p) =>
        p.id === sched.id
          ? { ...p, date, time: schedTime, status: 'scheduled' }
          : p),
    }
    setBusy(true)
    setSchedErr('')
    try {
      await backend.aimodels.writeBatch(next)
      setBatch(next)
      setDirty(false)
      setOk(S.scheduledOk)
      setSched(null)
    } catch (e) {
      setSchedErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (error && !batch) return <p className="error">{error}</p>
  if (!batch || !roster) return <p>{S.loading}</p>

  const schedChar = sched ? byId[sched.char] || {} : {}
  const monthDays = schedMonth ? new Date(schedMonth.y, schedMonth.m0 + 1, 0).getDate() : 0
  const monthOffset = schedMonth ? new Date(schedMonth.y, schedMonth.m0, 1).getDay() : 0
  const moveMonth = (d) =>
    setSchedMonth(({ y, m0 }) => {
      const dt = new Date(y, m0 + d, 1)
      setSchedDay(null)
      return { y: dt.getFullYear(), m0: dt.getMonth() }
    })

  return (
    <div className="aimodels">
      <div className="row" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <button className={'tab' + (view === 'batch' ? ' active' : '')} onClick={() => setView('batch')}>
          {S.viewBatch} ({counts.pending}/{batch.posts.length})
        </button>
        <button className={'tab' + (view === 'roster' ? ' active' : '')} onClick={() => setView('roster')}>
          {S.viewRoster}
        </button>
      </div>

      {view === 'roster' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(210px,1fr))', gap: 12 }}>
          {roster.map((c) => (
            <div key={c.id} style={{ border: '1px solid var(--border,#ccc)', borderRadius: 10, overflow: 'hidden' }}>
              <img src={c.casting_thumb || c.casting} alt={c.name} loading="lazy" decoding="async" style={{ width: '100%', aspectRatio: '3/4', objectFit: 'cover', display: 'block' }} />
              <div style={{ padding: '6px 10px', fontSize: 13 }}>
                <b>{c.name}</b> · {c.city}
                <div dir="ltr" style={{ textAlign: 'left', opacity: 0.75 }}>{c.handle}</div>
                <div style={{ opacity: 0.75 }}>{S.voice}: {c.voice}</div>
                <a href={c.sheet} target="_blank" rel="noreferrer">{S.openSheet}</a>
              </div>
            </div>
          ))}
        </div>
      )}

      {view === 'batch' && (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
            <b>{batch.week}</b>
            {batch.note && <span style={{ opacity: 0.7 }}>· {batch.note}</span>}
            <span style={{ flex: 1 }} />
            <button onClick={() => setImportOpen((v) => !v)}>{S.importBatch}</button>
            <button disabled={!dirty || busy} onClick={save}>
              {busy ? S.saving : S.saveDecisions}
            </button>
            <button disabled={counts.scheduled === 0} onClick={exportCsv}>
              {S.exportCsv} ({counts.scheduled})
            </button>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">{S.statusAll}</option>
              <option value="pending">{S.statuses.pending} ({counts.pending})</option>
              <option value="scheduled">{S.statuses.scheduled} ({counts.scheduled})</option>
              <option value="rejected">{S.statuses.rejected} ({counts.rejected})</option>
            </select>
            <select value={charFilter} onChange={(e) => setCharFilter(e.target.value)}>
              <option value="">{S.filterAll}</option>
              {(roster || []).map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
          {ok && <p className="ok">{ok}</p>}
          {error && <p className="error">{error}</p>}
          {importOpen && (
            <div style={{ marginBottom: 10 }}>
              <textarea
                dir="ltr"
                rows={6}
                style={{ width: '100%' }}
                placeholder={S.importHint}
                value={importText}
                onChange={(e) => setImportText(e.target.value)}
              />
              <button onClick={doImport}>{S.importApply}</button>
            </div>
          )}
          {groups.map(([charId, posts]) => {
            const gc = byId[charId] || {}
            const pendingN = posts.filter((p) => statusOf(p) === 'pending').length
            return (
            <div key={charId} style={{ borderTop: '2px solid var(--border,#ccc)', marginTop: 18, paddingTop: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                {gc.casting && (
                  <img src={gc.casting_thumb || gc.casting} alt="" decoding="async" style={{ width: 40, height: 40, borderRadius: '50%', objectFit: 'cover' }} />
                )}
                <h3 style={{ margin: 0 }}>{gc.name || charId}</h3>
                <span style={{ opacity: 0.65 }}>· {gc.city}</span>
                <span dir="ltr" style={{ opacity: 0.65 }}>{gc.handle}</span>
                {pendingN > 0 && (
                  <span style={{ background: '#b8860b22', border: '1px solid #b8860b', borderRadius: 12, padding: '0 10px', fontSize: 12 }}>
                    {pendingN} {S.pendingBadge}
                  </span>
                )}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(240px,1fr))', gap: 12 }}>
                {posts.map((p) => {
                  const c = byId[p.char] || {}
                  const st = statusOf(p)
                  const border =
                    st === 'scheduled' ? '2px solid #22a06b' : st === 'rejected' ? '2px solid #d33' : '1px solid var(--border,#ccc)'
                  return (
                    <div key={p.id} style={{ border, borderRadius: 10, overflow: 'hidden', opacity: st === 'rejected' ? 0.55 : 1, position: 'relative' }}>
                      <span style={{ position: 'absolute', top: 6, insetInlineStart: 6, background: 'rgba(0,0,0,.65)', color: '#fff', borderRadius: 6, padding: '1px 8px', fontSize: 12 }}>
                        {S.types[p.type || 'post'] || p.type}
                      </span>
                      {st === 'scheduled' && (
                        <span style={{ position: 'absolute', top: 6, insetInlineEnd: 6, background: '#22a06b', color: '#fff', borderRadius: 6, padding: '1px 8px', fontSize: 12 }}>
                          {S.scheduledBadge}
                        </span>
                      )}
                      <img src={p.thumb || p.image} alt="" loading="lazy" decoding="async" style={{ width: '100%', aspectRatio: '4/5', objectFit: 'cover', display: 'block' }} />
                      <div style={{ padding: '6px 10px', fontSize: 13 }}>
                        <b>{c.name || p.char}</b> · {p.date} · {p.time}
                        {p.generatedAt && (
                          <div style={{ fontSize: 11, opacity: 0.6 }}>
                            {S.createdAt}: <span dir="ltr">{fmtCreated(p.generatedAt)}</span>
                          </div>
                        )}
                        <div dir="ltr" style={{ textAlign: 'left', opacity: 0.85, minHeight: 34 }}>{p.caption}</div>
                        <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                          <button style={{ flex: 1, fontWeight: 600 }} onClick={() => openSchedule(p)} disabled={busy}>
                            ⬆ {S.schedule}
                          </button>
                          <button style={{ flex: 1 }} onClick={() => setStatus(p.id, 'rejected')} disabled={p.status === 'rejected'}>
                            ✗ {S.reject}
                          </button>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
            )
          })}
        </>
      )}

      {sched && schedMonth && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, zIndex: 50 }}
          onClick={(e) => { if (e.target === e.currentTarget) setSched(null) }}
        >
          <div style={{ background: '#fff', color: '#1c1e24', border: '1px solid #ccc', borderRadius: 14, width: '100%', maxWidth: 640, maxHeight: '90vh', overflow: 'auto', padding: '16px 18px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              {schedChar.casting && (
                <img src={schedChar.casting_thumb || schedChar.casting} alt="" decoding="async" style={{ width: 38, height: 38, borderRadius: '50%', objectFit: 'cover' }} />
              )}
              <div>
                <b>{S.calendarOf} {schedChar.name || sched.char}</b>
                <div style={{ fontSize: 12, opacity: 0.65 }}>
                  <span dir="ltr">{schedChar.handle}</span> · {schedChar.tz} · {S.onlyThisChar}
                </div>
              </div>
              <span style={{ flex: 1 }} />
              <button onClick={() => setSched(null)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer', color: 'inherit' }}>✕</button>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16, margin: '12px 0' }}>
              <button onClick={() => moveMonth(-1)}>‹</button>
              <b>{S.months[schedMonth.m0]} {schedMonth.y}</b>
              <button onClick={() => moveMonth(1)}>›</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 6 }}>
              {['א', 'ב', 'ג', 'ד', 'ה', 'ו', 'ש'].map((d) => (
                <div key={d} style={{ fontSize: 11, opacity: 0.6, textAlign: 'center' }}>{d}</div>
              ))}
              {Array.from({ length: monthOffset }, (_, i) => (
                <div key={`e${i}`} />
              ))}
              {Array.from({ length: monthDays }, (_, i) => {
                const d = i + 1
                const taken = occupied[d]
                const picked = schedDay === d
                return (
                  <div
                    key={d}
                    onClick={taken ? undefined : () => setSchedDay(d)}
                    style={{
                      aspectRatio: '1', border: picked ? '2px solid #6ea8fe' : '1px solid #ccc',
                      background: picked ? 'rgba(110,168,254,.15)' : taken ? '#f4f4f6' : 'transparent',
                      borderRadius: 8, padding: 3, position: 'relative', fontSize: 11,
                      cursor: taken ? 'default' : 'pointer', overflow: 'hidden',
                    }}
                  >
                    <span style={{ position: 'absolute', top: 2, insetInlineEnd: 5 }}>{d}</span>
                    {taken && (
                      <>
                        <img src={taken.thumb || taken.image} alt="" loading="lazy" decoding="async"
                          style={{ position: 'absolute', bottom: 14, insetInline: 3, height: '48%', width: 'calc(100% - 6px)', objectFit: 'cover', borderRadius: 4 }} />
                        <span style={{ position: 'absolute', bottom: 2, insetInline: 3, fontSize: 9, background: '#22a06b', color: '#fff', borderRadius: 4, textAlign: 'center' }}>
                          {S.scheduledBadge}
                        </span>
                      </>
                    )}
                  </div>
                )
              })}
            </div>

            <div style={{ display: 'flex', gap: 14, justifyContent: 'center', fontSize: 11, opacity: 0.7, marginTop: 10 }}>
              <span><i style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 3, background: '#22a06b', marginInlineStart: 4, verticalAlign: 'middle' }} /> {S.legendScheduled}</span>
              <span><i style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 3, background: 'rgba(110,168,254,.5)', marginInlineStart: 4, verticalAlign: 'middle' }} /> {S.legendPicked}</span>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 14 }}>
              <span>{S.publishTime}</span>
              <input type="time" value={schedTime} onChange={(e) => setSchedTime(e.target.value)} />
              <span style={{ flex: 1 }} />
              <button
                disabled={!schedDay || busy}
                onClick={confirmSchedule}
                style={{ background: '#22a06b', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 16px', fontWeight: 700, cursor: schedDay ? 'pointer' : 'default', opacity: schedDay ? 1 : 0.45 }}
              >
                {busy ? S.saving : schedDay
                  ? `${S.scheduleTo}${schedDay}.${schedMonth.m0 + 1} ${S.atHour} ${schedTime}`
                  : `${S.scheduleTo}— ${S.atHour} ${schedTime}`}
              </button>
            </div>
            {schedErr && <p className="error" style={{ marginTop: 8 }}>{schedErr}</p>}
            <p style={{ fontSize: 12, opacity: 0.6, marginTop: 8 }}>{S.calendarHint}</p>
          </div>
        </div>
      )}
    </div>
  )
}
