import { useCallback, useEffect, useRef, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import ResultCard from './ResultCard.jsx'

const ACTIVE_STATUSES = ['queued', 'downloading', 'processing', 'captioning']

export default function ResultsTab({ active }) {
  const [batches, setBatches] = useState([])
  const [assets, setAssets] = useState([])
  const [error, setError] = useState('')
  const timer = useRef(null)

  const refresh = useCallback(async () => {
    try {
      const data = await backend.listBatches()
      setBatches(data)
      setError('')
      return data
    } catch (e) {
      setError(e.message)
      return []
    }
  }, [])

  useEffect(() => {
    if (!active) {
      clearInterval(timer.current)
      return
    }
    backend.listAssets().then(setAssets).catch(() => {})
    refresh()
    timer.current = setInterval(async () => {
      const data = await refresh()
      const anyActive = data.some((b) =>
        b.requests.some((r) => ACTIVE_STATUSES.includes(r.status))
      )
      if (!anyActive) clearInterval(timer.current)
    }, backend.pollInterval())
    return () => clearInterval(timer.current)
  }, [active, refresh])

  const assetName = (id) =>
    id == null
      ? STR.results.pendingBatch
      : assets.find((a) => String(a.id) === String(id))?.name || `#${id}`

  if (batches.length === 0) {
    return (
      <div>
        {error && <p className="error">{STR.errors.generic}{error}</p>}
        <p className="empty">{STR.results.empty}</p>
      </div>
    )
  }

  const newestWithCaptions = batches.find((b) =>
    b.requests.some((r) => r.caption)
  )
  const showMockAlert = backend.mode === 'github' && newestWithCaptions?._mockCaptions

  return (
    <div className="results">
      <button className="secondary refresh" onClick={refresh}>
        {STR.results.refresh}
      </button>
      {showMockAlert && (
        <div className="mock-alert">
          <strong>{STR.results.mockAlertTitle}</strong>
          <p>{STR.results.mockAlertBody}</p>
          <a
            href={`https://github.com/${backend.repo}/settings/secrets/actions`}
            target="_blank"
            rel="noreferrer"
          >
            {STR.results.mockAlertAction}
          </a>
        </div>
      )}
      {error && <p className="error">{STR.errors.generic}{error}</p>}
      {batches.map((b) => (
        <section key={b.id} className="batch-section">
          <h2>
            {STR.results.batchTitle} #{b.id}
            <span className="muted"> · {b.created_at}</span>
          </h2>
          <div className="cards">
            {b.requests.map((r) => (
              <ResultCard
                key={r.id}
                request={r}
                assetName={assetName(r.asset_id)}
                onChanged={refresh}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
