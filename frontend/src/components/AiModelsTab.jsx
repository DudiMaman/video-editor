import { useEffect, useMemo, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const S = STR.aimodels

// Weekly approval flow for the AI models project:
// batch.json holds the current content batch (posts with pending/approved/
// rejected status). Decisions are saved back to the repo (token required),
// and approved posts export as a CSV ready for the social scheduler.

function csvEscape(v) {
  const s = String(v ?? '')
  return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s
}

export default function AiModelsTab({ active }) {
  const [view, setView] = useState('batch') // batch | roster
  const [roster, setRoster] = useState(null)
  const [batch, setBatch] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')

  useEffect(() => {
    if (!active || roster !== null) return
    if (!backend.aimodels) {
      setError(S.githubOnly)
      return
    }
    Promise.all([backend.aimodels.readRoster(), backend.aimodels.readBatch()])
      .then(([r, b]) => {
        setRoster(r)
        setBatch(b)
      })
      .catch((e) => setError(e.message))
  }, [active])

  const byId = useMemo(() => {
    const m = {}
    for (const c of roster || []) m[c.id] = c
    return m
  }, [roster])

  const counts = useMemo(() => {
    const c = { pending: 0, approved: 0, rejected: 0 }
    for (const p of batch?.posts || []) c[p.status] = (c[p.status] || 0) + 1
    return c
  }, [batch])

  const setStatus = (id, status) => {
    setBatch((b) => ({
      ...b,
      posts: b.posts.map((p) => (p.id === id ? { ...p, status } : p)),
    }))
    setDirty(true)
    setOk('')
  }

  const setAll = (status) => {
    setBatch((b) => ({ ...b, posts: b.posts.map((p) => ({ ...p, status })) }))
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

  const exportCsv = () => {
    const rows = [['account', 'date', 'time', 'timezone', 'text', 'image_url']]
    for (const p of batch.posts) {
      if (p.status !== 'approved') continue
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

  if (error && !batch) return <p className="error">{error}</p>
  if (!batch || !roster) return <p>{S.loading}</p>

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
              <img src={c.casting} alt={c.name} style={{ width: '100%', aspectRatio: '3/4', objectFit: 'cover', display: 'block' }} />
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
            <button onClick={() => setAll('approved')}>{S.approveAll}</button>
            <button onClick={() => setImportOpen((v) => !v)}>{S.importBatch}</button>
            <button disabled={!dirty || busy} onClick={save}>
              {busy ? S.saving : S.saveDecisions}
            </button>
            <button disabled={counts.approved === 0} onClick={exportCsv}>
              {S.exportCsv} ({counts.approved})
            </button>
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(240px,1fr))', gap: 12 }}>
            {batch.posts.map((p) => {
              const c = byId[p.char] || {}
              const border =
                p.status === 'approved' ? '2px solid #22a06b' : p.status === 'rejected' ? '2px solid #d33' : '1px solid var(--border,#ccc)'
              return (
                <div key={p.id} style={{ border, borderRadius: 10, overflow: 'hidden', opacity: p.status === 'rejected' ? 0.55 : 1 }}>
                  <img src={p.image} alt="" loading="lazy" style={{ width: '100%', aspectRatio: '4/5', objectFit: 'cover', display: 'block' }} />
                  <div style={{ padding: '6px 10px', fontSize: 13 }}>
                    <b>{c.name || p.char}</b> · {p.date} · {p.time}
                    <div dir="ltr" style={{ textAlign: 'left', opacity: 0.85, minHeight: 34 }}>{p.caption}</div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                      <button style={{ flex: 1 }} onClick={() => setStatus(p.id, 'approved')} disabled={p.status === 'approved'}>
                        ✓ {S.approve}
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
        </>
      )}
    </div>
  )
}
