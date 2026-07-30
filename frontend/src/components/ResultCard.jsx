import { useState } from 'react'
import { STR } from '../strings.js'
import { post } from '../api.js'

export default function ResultCard({ request, assetName, onChanged }) {
  const [copied, setCopied] = useState(false)
  const r = request

  const copy = async () => {
    await navigator.clipboard.writeText(r.caption)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const recaption = async () => {
    try {
      await post(`/api/requests/${r.id}/recaption`)
      onChanged()
    } catch (e) {
      alert(e.message)
    }
  }

  return (
    <div className={`card result-card status-${r.status}`}>
      <div className="result-head">
        <strong>{assetName}</strong>
        <span className="muted" dir="ltr">✂ {r.cut_seconds}s</span>
        <span className={`badge badge-${r.status}`}>{STR.status[r.status]}</span>
      </div>

      {r.source_url && (
        <p className="muted source-url" dir="ltr">{r.source_url}</p>
      )}

      {r.error && <p className={r.status === 'failed' ? 'error' : 'warning'}>{r.error}</p>}

      {r.status === 'done' && r.has_output && (
        <>
          <video controls preload="metadata" src={`/api/requests/${r.id}/video`} />
          <div className="result-actions">
            <a className="button secondary" href={`/api/requests/${r.id}/video?download=1`}>
              {STR.results.download}
            </a>
            {r.caption ? (
              <button className="secondary" onClick={copy}>
                {copied ? STR.results.copied : STR.results.copyCaption}
              </button>
            ) : (
              <button className="secondary" onClick={recaption}>
                {STR.results.recaption}
              </button>
            )}
          </div>
          {r.caption ? (
            <textarea dir="ltr" readOnly value={r.caption} rows={6} />
          ) : (
            <p className="muted">{STR.results.captionMissing}</p>
          )}
        </>
      )}
    </div>
  )
}
