import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const S = STR.scout

const emptyForm = () => ({ url: '', rights_basis: '', rights_note: '', notes: '' })

const nextPageId = (pages) => {
  const max = pages.reduce((m, p) => {
    const n = parseInt(String(p.id || '').replace('pg_', ''), 10)
    return Number.isFinite(n) && n > m ? n : m
  }, 0)
  return `pg_${String(max + 1).padStart(3, '0')}`
}

export default function InboxTab({ active }) {
  const [pages, setPages] = useState(null)
  const [form, setForm] = useState(emptyForm())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  const load = () =>
    backend.scout.readInbox().then(setPages).catch((e) => setError(e.message))

  useEffect(() => {
    if (active) load()
  }, [active])

  const add = async () => {
    setOk('')
    if (!form.url.trim()) return setError(S.errMissingUrl)
    if (!form.rights_basis) return setError(S.errMissingRights)
    setError('')
    setBusy(true)
    try {
      const updated = await backend.scout.mutateInbox((list) => {
        list.push({
          id: nextPageId(list),
          url: form.url.trim(),
          added_at: new Date().toISOString().slice(0, 10),
          rights_basis: form.rights_basis,
          rights_note: form.rights_note.trim(),
          active: true,
          notes: form.notes.trim(),
        })
      }, `Scout inbox: add ${form.url.trim()}`)
      setPages(updated)
      setForm(emptyForm())
      setOk(S.added + ' ✓')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleActive = async (page) => {
    setError('')
    try {
      const updated = await backend.scout.mutateInbox((list) => {
        const p = list.find((x) => x.id === page.id)
        if (!p) return false
        p.active = !p.active
      }, `Scout inbox: ${page.active ? 'deactivate' : 'activate'} ${page.id}`)
      setPages(updated)
    } catch (e) {
      setError(e.message)
    }
  }

  if (pages === null) return <p className="empty">…</p>

  return (
    <div>
      <div className="card">
        <h3>{S.inboxTitle}</h3>
        <p className="hint">{S.inboxExplain}</p>
        <div className="fields">
          <label>
            {S.pageUrl}
            <input
              type="url"
              dir="ltr"
              placeholder="https://..."
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
            />
          </label>
          <label>
            {S.rightsBasis}
            <select
              value={form.rights_basis}
              onChange={(e) => setForm({ ...form, rights_basis: e.target.value })}
            >
              <option value="">—</option>
              {Object.entries(S.rightsOptions).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>
          <label>
            {S.rightsNote}
            <input
              value={form.rights_note}
              onChange={(e) => setForm({ ...form, rights_note: e.target.value })}
            />
          </label>
          <label>
            {S.pageNotes}
            <input
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </label>
        </div>
        <div className="batch-actions">
          <button className="primary" onClick={add} disabled={busy}>
            {S.addPage}
          </button>
        </div>
        {error && <p className="error">{STR.errors.generic}{error}</p>}
        {ok && <p className="ok">{ok}</p>}
      </div>

      {pages.length === 0 ? (
        <p className="empty">{S.inboxEmpty}</p>
      ) : (
        pages.map((p) => (
          <div key={p.id} className={'card inbox-page' + (p.active ? '' : ' inactive')}>
            <div className="result-head">
              <strong dir="ltr" className="inbox-url">
                <a href={p.url} target="_blank" rel="noreferrer">{p.url}</a>
              </strong>
              <span className={'badge ' + (p.active ? 'badge-done' : 'badge-failed')}>
                {p.active ? S.active : S.inactive}
              </span>
            </div>
            <p className="muted">
              {S.rightsOptions[p.rights_basis] || p.rights_basis}
              {p.rights_note ? ` — ${p.rights_note}` : ''}
              {' · '}{S.added} {p.added_at}
            </p>
            {p.notes && <p className="hint">{p.notes}</p>}
            <button className="secondary small" onClick={() => toggleActive(p)}>
              {p.active ? S.deactivate : S.activate}
            </button>
          </div>
        ))
      )}
    </div>
  )
}
