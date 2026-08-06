import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const S = STR.scout

const emptyForm = () => ({ urls: '', asset: '' })

// Same canonical form as scripts/discover_pages.py norm_url.
const normUrl = (u) =>
  String(u || '')
    .trim()
    .toLowerCase()
    .replace(/[?#].*$/, '')
    .replace(/\/+$/, '')
    .replace(/^https?:\/\//, '')
    .replace(/^(www\.|m\.)/, '')

const nextPageId = (pages) => {
  const max = pages.reduce((m, p) => {
    const n = parseInt(String(p.id || '').replace('pg_', ''), 10)
    return Number.isFinite(n) && n > m ? n : m
  }, 0)
  return `pg_${String(max + 1).padStart(3, '0')}`
}

export default function InboxTab({ active }) {
  const [pages, setPages] = useState(null)
  const [assets, setAssets] = useState([])
  const [suggestions, setSuggestions] = useState([])
  const [appFilter, setAppFilter] = useState('')
  const [form, setForm] = useState(emptyForm())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')
  const [discoverMsg, setDiscoverMsg] = useState('')
  const [dailyOn, setDailyOn] = useState(true)

  useEffect(() => {
    if (!active) return
    backend.scout.readInbox().then(setPages).catch((e) => setError(e.message))
    backend.scout.readSuggestions().then(setSuggestions).catch(() => {})
    backend.scout
      .readConfig()
      .then((c) => setDailyOn(c.discovery_daily !== false))
      .catch(() => {})
    backend.listAssets().then(setAssets).catch(() => {})
  }, [active])

  const toggleDaily = async () => {
    setError('')
    try {
      await backend.scout.setDiscoveryDaily(!dailyOn)
      setDailyOn(!dailyOn)
    } catch (e) {
      setError(e.message)
    }
  }

  const assetName = (id) =>
    assets.find((a) => String(a.id) === String(id))?.name || id || S.unknownAsset

  const add = async () => {
    setOk('')
    const urls = form.urls
      .split('\n')
      .map((u) => u.trim())
      .filter((u) => /^https?:\/\//i.test(u))
    if (urls.length === 0) return setError(S.errNoValidUrls)
    if (!form.asset) return setError(S.errMissingAssetPage)
    setError('')
    setBusy(true)
    let added = 0
    let skipped = 0
    try {
      const updated = await backend.scout.mutateInbox((list) => {
        added = 0
        skipped = 0
        const known = new Set(list.map((p) => normUrl(p.url)))
        for (const url of urls) {
          if (known.has(normUrl(url))) {
            skipped++
            continue
          }
          known.add(normUrl(url))
          list.push({
            id: nextPageId(list),
            url,
            asset: form.asset,
            added_at: new Date().toISOString().slice(0, 10),
            active: true,
          })
          added++
        }
        if (!added) return false
      }, `Scout inbox: add page(s)`)
      setPages(updated)
      setForm(emptyForm())
      setOk(
        [added ? S.addedCount(added) : '', skipped ? S.skippedDup(skipped) : '']
          .filter(Boolean)
          .join(' · ')
      )
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

  const runDiscovery = async () => {
    setError('')
    try {
      await backend.scout.runDiscovery()
      setDiscoverMsg(S.discoverRunning)
    } catch (e) {
      setError(e.message)
    }
  }

  const acceptSuggestion = async (s) => {
    setError('')
    try {
      const updatedPages = await backend.scout.mutateInbox((list) => {
        if (list.some((p) => p.url === s.url)) return false
        list.push({
          id: nextPageId(list),
          url: s.url,
          asset: s.asset,
          added_at: new Date().toISOString().slice(0, 10),
          active: true,
        })
      }, `Scout inbox: accept suggestion ${s.url}`)
      const updatedSugs = await backend.scout.mutateSuggestions((list) => {
        const x = list.find((y) => y.url === s.url)
        if (!x) return false
        x.status = 'accepted'
      }, `Scout suggestions: accept ${s.url}`)
      setPages(updatedPages)
      setSuggestions(updatedSugs)
      setOk(S.suggestionAccepted)
    } catch (e) {
      setError(e.message)
    }
  }

  const dismissSuggestion = async (s) => {
    setError('')
    try {
      const updated = await backend.scout.mutateSuggestions((list) => {
        const x = list.find((y) => y.url === s.url)
        if (!x) return false
        x.status = 'dismissed'
      }, `Scout suggestions: dismiss ${s.url}`)
      setSuggestions(updated)
    } catch (e) {
      setError(e.message)
    }
  }

  const removePage = async (page) => {
    if (!confirm(S.confirmRemovePage)) return
    setError('')
    try {
      const updated = await backend.scout.mutateInbox((list) => {
        const idx = list.findIndex((x) => x.id === page.id)
        if (idx === -1) return false
        list.splice(idx, 1)
      }, `Scout inbox: remove ${page.id}`)
      setPages(updated)
    } catch (e) {
      setError(e.message)
    }
  }

  if (pages === null) return <p className="empty">…</p>

  const shown = appFilter
    ? pages.filter((p) => String(p.asset) === appFilter)
    : pages

  return (
    <div>
      <div className="card">
        <h3>{S.inboxTitle}</h3>
        <p className="hint">{S.inboxExplain}</p>
        {assets.length === 0 && <p className="warning">{S.noAssetsYet}</p>}
        <div className="fields inbox-form">
          <label className="inbox-urls-field">
            {S.pageUrls}
            <textarea
              dir="ltr"
              rows={4}
              placeholder={S.pageUrlsPlaceholder}
              value={form.urls}
              onChange={(e) => setForm({ ...form, urls: e.target.value })}
            />
          </label>
          <label>
            {S.pageAsset}
            <select
              value={form.asset}
              onChange={(e) => setForm({ ...form, asset: e.target.value })}
            >
              <option value="">—</option>
              {assets.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
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

      <div className="card discover-card">
        <div className="discover-info">
          <h3>{S.discoverTitle}</h3>
          <p className="hint">{S.discoverDesc}</p>
        </div>
        <div className="discover-controls">
          <div className={'discover-daily' + (dailyOn ? '' : ' off')}>
            <span className={'badge ' + (dailyOn ? 'badge-done' : 'badge-failed')}>
              {dailyOn ? S.dailyOn : S.dailyOff}
            </span>
            <span className="muted discover-daily-desc">
              {dailyOn ? S.dailyOnDesc : S.dailyOffDesc}
            </span>
            <button
              className={(dailyOn ? 'secondary' : 'primary') + ' small'}
              onClick={toggleDaily}
            >
              {dailyOn ? S.pauseDaily : S.resumeDaily}
            </button>
          </div>
          <button className="primary" onClick={runDiscovery} disabled={pages?.length === 0}>
            🔍 {S.discoverBtn}
          </button>
        </div>
        {discoverMsg && <p className="ok discover-msg">{discoverMsg}</p>}
      </div>

      {suggestions.filter((s) => s.status === 'suggested').length > 0 && (
        <div className="card suggestions-card">
          <h3>{S.suggestionsTitle}</h3>
          <p className="hint">{S.suggestionsHint}</p>
          {suggestions
            .filter((s) => s.status === 'suggested')
            .map((s) => (
              <div key={s.url} className="inbox-row suggestion-row">
                <div className="suggestion-main">
                  <a className="inbox-row-url" dir="ltr" href={s.url} target="_blank" rel="noreferrer">
                    {s.url}
                  </a>
                  <span className="muted suggestion-reason">
                    {assetName(s.asset)}{s.reason ? ` · ${s.reason}` : ''}
                  </span>
                </div>
                <button className="primary small" onClick={() => acceptSuggestion(s)}>
                  {S.acceptSuggestion}
                </button>
                <button className="secondary small" onClick={() => dismissSuggestion(s)}>
                  {S.dismissSuggestion}
                </button>
              </div>
            ))}
        </div>
      )}

      {pages.length > 0 && (
        <div className="log-filter">
          <button
            className={'tab' + (appFilter === '' ? ' active' : '')}
            onClick={() => setAppFilter('')}
          >
            {S.allApps} ({pages.length})
          </button>
          {assets
            .filter((a) => pages.some((p) => String(p.asset) === String(a.id)))
            .map((a) => (
              <button
                key={a.id}
                className={'tab' + (appFilter === String(a.id) ? ' active' : '')}
                onClick={() => setAppFilter(String(a.id))}
              >
                {a.name} ({pages.filter((p) => String(p.asset) === String(a.id)).length})
              </button>
            ))}
        </div>
      )}

      {pages.length === 0 ? (
        <p className="empty">{S.inboxEmpty}</p>
      ) : (
        groupByApp(shown, assets).map((g) => (
          <section key={g.id || 'none'} className="inbox-group">
            <h3 className="inbox-group-title">
              {g.name} <span className="muted">({g.list.length})</span>
            </h3>
            <div className="card inbox-table">
              {g.list.map((p) => (
                <div key={p.id} className={'inbox-row' + (p.active ? '' : ' inactive')}>
                  <a
                    className="inbox-row-url"
                    dir="ltr"
                    href={p.url}
                    target="_blank"
                    rel="noreferrer"
                    title={[p.notes, p.rights_note].filter(Boolean).join(' · ')}
                  >
                    {p.url}
                  </a>
                  <button
                    className={'badge badge-btn ' + (p.active ? 'badge-done' : 'badge-failed')}
                    onClick={() => toggleActive(p)}
                    title={S.toggleHint}
                  >
                    {p.active ? S.active : S.inactive}
                  </button>
                  <button className="secondary small" onClick={() => removePage(p)}>
                    {S.removePage}
                  </button>
                </div>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  )
}

// One group per app (registry order), pages without a known app last.
function groupByApp(pages, assets) {
  const groups = []
  for (const a of assets) {
    const list = pages.filter((p) => String(p.asset) === String(a.id))
    if (list.length) groups.push({ id: String(a.id), name: a.name, list })
  }
  const known = new Set(assets.map((a) => String(a.id)))
  const orphans = pages.filter((p) => !known.has(String(p.asset)))
  if (orphans.length) groups.push({ id: '', name: S.unknownAsset, list: orphans })
  return groups
}
