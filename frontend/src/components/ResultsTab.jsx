import { useCallback, useEffect, useRef, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import { generateCaptionsForBatch, getAnthropicKey } from '../captionsClient.js'
import ResultCard from './ResultCard.jsx'

const ACTIVE_STATUSES = ['queued', 'downloading', 'processing', 'captioning']

export default function ResultsTab({ active }) {
  const [batches, setBatches] = useState([])
  const [assets, setAssets] = useState([])
  const [error, setError] = useState('')
  const [captioning, setCaptioning] = useState('')
  const timer = useRef(null)
  const captioningRef = useRef(false)

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

  const assetsRef = useRef([])
  assetsRef.current = assets
  const autoFailedRef = useRef(false)

  // Regenerate captions in the browser for every batch still carrying
  // placeholder captions, then refresh from the updated releases.
  const runSmartCaptions = useCallback(async (data, manual = false) => {
    if (captioningRef.current || backend.mode !== 'github') return
    if (!getAnthropicKey() || !backend.hasToken()) return
    if (autoFailedRef.current && !manual) return
    if (manual) autoFailedRef.current = false
    const mockBatches = (data || []).filter(
      (b) => b._mockCaptions && b.requests.some((r) => r.status === 'done')
    )
    if (!mockBatches.length) return
    captioningRef.current = true
    try {
      for (const b of mockBatches) {
        await generateCaptionsForBatch(backend, b, assetsRef.current, (i, n) =>
          setCaptioning(`${STR.captions.progress} ${STR.results.batchTitle} #${b.id} — ${i}/${n}`)
        )
      }
      setCaptioning('')
      setError('')
    } catch (e) {
      setCaptioning('')
      setError(`${STR.captions.failed}: ${e.message}`)
      autoFailedRef.current = true
    } finally {
      captioningRef.current = false
      refresh()
    }
  }, [refresh])

  useEffect(() => {
    if (active && batches.length && assets.length) runSmartCaptions(batches)
  }, [active, batches, assets, runSmartCaptions])

  const newestWithCaptions = batches.find((b) =>
    b.requests.some((r) => r.caption)
  )
  const showMockAlert =
    backend.mode === 'github' && newestWithCaptions?._mockCaptions && !captioning

  if (batches.length === 0) {
    return (
      <div>
        {error && <p className="error">{STR.errors.generic}{error}</p>}
        <p className="empty">{STR.results.empty}</p>
      </div>
    )
  }

  return (
    <div className="results">
      <button className="secondary refresh" onClick={refresh}>
        {STR.results.refresh}
      </button>
      {captioning && <p className="ok">{captioning}</p>}
      {showMockAlert && (
        <div className="mock-alert">
          <strong>{STR.results.mockAlertTitle}</strong>
          <p>{STR.captions.alertBody}</p>
          <button className="primary" onClick={() => runSmartCaptions(batches, true)}>
            {STR.captions.runNow}
          </button>
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
