import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const Z = STR.zernioInbox

const PLATFORM_LABEL = {
  instagram: 'Instagram', tiktok: 'TikTok', facebook: 'Facebook',
  youtube: 'YouTube', twitter: 'X', threads: 'Threads', linkedin: 'LinkedIn',
  pinterest: 'Pinterest',
}
const platLabel = (v) => PLATFORM_LABEL[v] || v || '—'

// ISO ...Z -> "DD.MM.YYYY HH:MM" in the viewer's local time.
const fmtWhen = (v) => {
  const d = new Date(v)
  if (isNaN(d)) return String(v || '')
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}`
}

const isTransient = (n) => n.remedy === 'transient'

// The unified feed of Zernio distribution failures across every venture /
// character account, written server-side by the distributor Actions
// (data/zernio_inbox.json). Read-only: the owner watches it instead of
// hunting through Zernio's e-mails, and transient (capacity) failures are
// flagged as self-healing so they are not mistaken for real problems.
export default function ZernioInboxTab({ active }) {
  const [notices, setNotices] = useState(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all') // all | attention | transient

  const load = () =>
    backend.readZernioInbox()
      .then((list) => setNotices(Array.isArray(list) ? list : []))
      .catch((e) => setError(e.message))

  useEffect(() => {
    if (!active) return
    load()
  }, [active])

  if (error) return <p className="error">{STR.errors.generic}{error}</p>
  if (notices === null) return <p className="empty">…</p>

  // Newest first.
  const sorted = [...notices].sort((a, b) =>
    String(b.ts || '').localeCompare(String(a.ts || '')))
  const attention = sorted.filter((n) => !isTransient(n))
  const transient = sorted.filter(isTransient)
  const shown = filter === 'attention' ? attention
    : filter === 'transient' ? transient : sorted

  const chip = (key, label, n) => (
    <button
      className={'tab' + (filter === key ? ' active' : '')}
      onClick={() => setFilter(key)}
    >
      {label} ({n})
    </button>
  )

  return (
    <div>
      <h2>{Z.title}</h2>
      <p className="hint">{Z.explain}</p>

      {sorted.length > 0 && (
        <div className="log-filter">
          {chip('all', Z.filterAll, sorted.length)}
          {chip('attention', Z.filterErrors, attention.length)}
          {chip('transient', Z.filterTransient, transient.length)}
          <button className="tab" onClick={load} title={Z.title}>⟳</button>
        </div>
      )}

      {shown.length === 0 ? (
        <p className="empty">{Z.empty}</p>
      ) : (
        shown.map((n) => (
          <div
            key={n.id}
            className="card"
            style={{
              borderInlineStart: `4px solid ${isTransient(n) ? '#b8860b' : '#d33'}`,
              padding: '12px 14px', marginBottom: 10,
            }}
          >
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
              <strong>{n.ventureName || n.venture || '—'}</strong>
              {n.platform && (
                <span style={{ fontSize: 12, background: '#eef', color: '#334', borderRadius: 6, padding: '1px 8px' }}>
                  {platLabel(n.platform)}
                </span>
              )}
              <span
                style={{
                  fontSize: 12, fontWeight: 700, borderRadius: 6, padding: '1px 8px',
                  background: isTransient(n) ? '#fff4d6' : '#fde2e2',
                  color: isTransient(n) ? '#8a6100' : '#a11',
                }}
              >
                {isTransient(n) ? Z.transientBadge : Z.needsAttention}
              </span>
              <span style={{ flex: 1 }} />
              <span className="muted" dir="ltr" style={{ fontSize: 12 }}>{fmtWhen(n.ts)}</span>
            </div>

            <p dir="ltr" style={{ fontFamily: 'monospace', fontSize: 13, margin: '8px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {n.message}
            </p>

            {isTransient(n) && (
              <p className="hint" style={{ margin: '2px 0', color: '#8a6100' }}>{Z.transientHint}</p>
            )}

            {n.contentPreview && (
              <p className="muted" dir="ltr" style={{ fontSize: 12, margin: '4px 0' }}>
                “{n.contentPreview}”
              </p>
            )}

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, fontSize: 12, opacity: 0.75, marginTop: 4 }}>
              {n.scheduledFor && <span>{Z.scheduledForLabel} {fmtWhen(n.scheduledFor)}</span>}
              {n.postId && <span dir="ltr">{Z.postIdLabel}: {n.postId}</span>}
              {n.postUrl && (
                <a href={n.postUrl} target="_blank" rel="noreferrer">{Z.viewPost}</a>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
