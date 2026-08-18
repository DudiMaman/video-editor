import { useState } from 'react'

// Reusable month-grid scheduling modal (the apps-side Gantt, modeled on
// the AI-models calendar): occupied days render a thumbnail + status
// tag and cannot be picked; free days pick a slot; month navigation
// crosses years. The caller supplies everything data-related, so the
// component stays project-agnostic.
//
// props:
//   header      node rendered at the top (title, avatar, hint)
//   occupied    { 'YYYY-MM-DD': {img, tag, tagColor} }
//   initialDate 'YYYY-MM-DD' whose month opens first (falls back to today)
//   defaultTime 'HH:MM'
//   months      array of 12 localized month names
//   confirmLabel(day, m1, time) -> button text
//   legend      [{color, label}]
//   onConfirm(dateStr, time)
//   onClose()
//   busy        disables the confirm button while saving

const pad2 = (n) => String(n).padStart(2, '0')

export default function ScheduleCalendar({
  header, occupied = {}, initialDate, defaultTime = '18:00', months,
  confirmLabel, legend = [], onConfirm, onClose, busy = false,
}) {
  const base = /^\d{4}-\d{2}/.test(String(initialDate || ''))
    ? { y: +initialDate.slice(0, 4), m0: +initialDate.slice(5, 7) - 1 }
    : { y: new Date().getFullYear(), m0: new Date().getMonth() }
  const [month, setMonth] = useState(base)
  const [day, setDay] = useState(null)
  const [time, setTime] = useState(defaultTime)

  const moveMonth = (dir) => {
    setDay(null)
    setMonth(({ y, m0 }) => {
      const m = m0 + dir
      return m < 0 ? { y: y - 1, m0: 11 } : m > 11 ? { y: y + 1, m0: 0 } : { y, m0: m }
    })
  }

  const monthDays = new Date(month.y, month.m0 + 1, 0).getDate()
  const monthOffset = new Date(month.y, month.m0, 1).getDay()
  const keyOf = (d) => `${month.y}-${pad2(month.m0 + 1)}-${pad2(d)}`

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, zIndex: 55 }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{ background: '#fff', color: '#1c1e24', border: '1px solid #ccc', borderRadius: 14, width: '100%', maxWidth: 520, padding: '14px 16px', maxHeight: '92vh', overflowY: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1 }}>{header}</div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer' }}>✕</button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 14, margin: '8px 0' }}>
          <button onClick={() => moveMonth(-1)}>‹</button>
          <b>{months[month.m0]} {month.y}</b>
          <button onClick={() => moveMonth(1)}>›</button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 6 }}>
          {['א', 'ב', 'ג', 'ד', 'ה', 'ו', 'ש'].map((d) => (
            <div key={d} style={{ fontSize: 11, opacity: 0.6, textAlign: 'center' }}>{d}</div>
          ))}
          {Array.from({ length: monthOffset }, (_, i) => <div key={`e${i}`} />)}
          {Array.from({ length: monthDays }, (_, i) => {
            const d = i + 1
            const taken = occupied[keyOf(d)]
            const picked = day === d
            return (
              <div
                key={d}
                onClick={taken ? undefined : () => setDay(d)}
                style={{
                  aspectRatio: '1', border: picked ? '2px solid #6ea8fe' : '1px solid #ccc',
                  background: picked ? 'rgba(110,168,254,.15)' : taken ? '#f4f4f6' : 'transparent',
                  borderRadius: 8, padding: 3, position: 'relative', fontSize: 11,
                  cursor: taken ? 'default' : 'pointer', overflow: 'hidden',
                }}
              >
                <span style={{ position: 'absolute', top: 2, insetInlineEnd: 5, zIndex: 1 }}>{d}</span>
                {taken && (
                  <>
                    {taken.img && (
                      <img src={taken.img} alt="" loading="lazy" decoding="async"
                        style={{ position: 'absolute', bottom: 14, insetInline: 3, height: '60%', width: 'calc(100% - 6px)', objectFit: 'cover', borderRadius: 4 }} />
                    )}
                    <span style={{ position: 'absolute', bottom: 2, insetInline: 3, fontSize: 9, background: taken.tagColor || '#22a06b', color: '#fff', borderRadius: 4, textAlign: 'center', zIndex: 1 }}>
                      {taken.tag}
                    </span>
                  </>
                )}
              </div>
            )
          })}
        </div>

        {legend.length > 0 && (
          <div style={{ display: 'flex', gap: 14, justifyContent: 'center', fontSize: 11, opacity: 0.7, marginTop: 10 }}>
            {legend.map((l) => (
              <span key={l.label}>
                <i style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 3, background: l.color, marginInlineStart: 4, verticalAlign: 'middle' }} /> {l.label}
              </span>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 14 }}>
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
          <span style={{ flex: 1 }} />
          <button
            disabled={!day || busy}
            onClick={() => onConfirm(keyOf(day), time)}
            style={{ background: '#22a06b', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 16px', fontWeight: 700, cursor: day ? 'pointer' : 'default', opacity: day ? 1 : 0.45 }}
          >
            {confirmLabel(day, month.m0 + 1, time)}
          </button>
        </div>
      </div>
    </div>
  )
}
