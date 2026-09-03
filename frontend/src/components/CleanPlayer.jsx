import { useEffect, useRef, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const S = STR.scout

// Plays the clean, watermark-free copy of a curated video in a real media
// player (native controls + scrubber). If no preview exists yet, offers to
// prepare one and polls the ledger until the pipeline attaches it.
// Preview preparation is not guaranteed to finish: the source platform
// can refuse the download (TikTok did, repo-wide, on 2026-09-03). Give
// up after this many polls and hand the reviewer the source link, so a
// broken pipeline never blocks approving or rejecting.
const MAX_POLLS = 12 // x20s = 4 minutes

export default function CleanPlayer({ entry }) {
  const [asset, setAsset] = useState(entry.preview_asset || null)
  const [url, setUrl] = useState(undefined) // undefined=loading, null=none
  const [gaveUp, setGaveUp] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const poll = useRef(null)
  const dispatched = useRef(false)

  // Resolve an existing preview to a playable URL.
  useEffect(() => {
    if (!asset) return
    let alive = true
    backend.scout
      .resolveOutput(asset)
      .then((u) => alive && setUrl(u ? u.videoUrl : null))
      .catch(() => alive && setUrl(null))
    return () => {
      alive = false
    }
  }, [asset])

  // No preview yet: prepare one automatically (no button) and poll for
  // it, until it arrives or MAX_POLLS gives up.
  useEffect(() => {
    if (asset) return
    dispatched.current = true
    setGaveUp(false)
    backend.submitPreview(entry).catch(() => {})
    let polls = 0
    poll.current = setInterval(async () => {
      if (++polls > MAX_POLLS) {
        clearInterval(poll.current)
        setGaveUp(true)
        return
      }
      try {
        const list = await backend.scout.readLedger()
        const e = list.find((x) => x.video_id === entry.video_id)
        if (e?.preview_asset) {
          clearInterval(poll.current)
          setAsset(e.preview_asset)
        }
      } catch {
        /* keep polling */
      }
    }, 20000)
    return () => clearInterval(poll.current)
  }, [asset, entry, attempt])

  if (url) {
    return <video className="clean-player" controls preload="metadata" src={url} />
  }
  if (gaveUp) {
    return (
      <div className="clean-player-prep">
        <p className="muted">{S.previewFailed}</p>
        {entry.source_url && (
          <a href={entry.source_url} target="_blank" rel="noreferrer">
            {S.openOnTikTok}
          </a>
        )}
        <button type="button" onClick={() => setAttempt((n) => n + 1)}>
          {S.previewRetry}
        </button>
      </div>
    )
  }
  return (
    <div className="clean-player-prep">
      <p className="muted">{S.previewPreparing}</p>
    </div>
  )
}
