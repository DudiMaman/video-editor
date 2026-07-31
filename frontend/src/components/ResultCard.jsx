import { useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import ShareBar from './ShareBar.jsx'

export default function ResultCard({ request, assetName, onChanged }) {
  const [copied, setCopied] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const r = request

  // Controlled single-shot download: a plain <a href> can fire the download
  // twice on some browser/redirect combinations, so navigate programmatically
  // and debounce repeat clicks.
  const download = () => {
    if (downloading) return
    setDownloading(true)
    window.location.assign(backend.downloadUrl(r))
    setTimeout(() => setDownloading(false), 2500)
  }

  const copy = async () => {
    await navigator.clipboard.writeText(r.caption)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const recaption = async () => {
    try {
      await backend.recaption(r)
      onChanged()
    } catch (e) {
      alert(e.message)
    }
  }

  return (
    <div className={`card result-card status-${r.status}`}>
      <div className="result-head">
        <strong>{assetName}</strong>
        {r.cut_seconds != null && (
          <span className="muted" dir="ltr">✂ {r.cut_seconds}s</span>
        )}
        <span className={`badge badge-${r.status}`}>{STR.status[r.status]}</span>
      </div>

      {r.source_url && (
        <p className="muted source-url" dir="ltr">{r.source_url}</p>
      )}

      {r.error && (
        <p className={r.status === 'failed' ? 'error' : 'warning'}>{r.error}</p>
      )}

      {r._runUrl && (
        <a href={r._runUrl} target="_blank" rel="noreferrer" className="muted">
          {STR.github.viewLog}
        </a>
      )}

      {r.status === 'done' && r.has_output && (
        <>
          <video controls preload="metadata" src={backend.videoUrl(r)} />
          <div className="result-actions">
            <button className="secondary" onClick={download} disabled={downloading}>
              {STR.results.download}
            </button>
            {r.caption ? (
              <button className="secondary" onClick={copy}>
                {copied ? STR.results.copied : STR.results.copyCaption}
              </button>
            ) : (
              backend.supportsRecaption && (
                <button className="secondary" onClick={recaption}>
                  {STR.results.recaption}
                </button>
              )
            )}
          </div>
          {r.caption ? (
            <textarea dir="ltr" readOnly value={r.caption} rows={6} />
          ) : (
            <p className="muted">{STR.results.captionMissing}</p>
          )}
          <ShareBar request={r} />
        </>
      )}
    </div>
  )
}
