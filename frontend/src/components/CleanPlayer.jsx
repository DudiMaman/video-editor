import { useEffect, useRef, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const S = STR.scout

// Plays the clean, watermark-free copy of a curated video in a real media
// player (native controls + scrubber). If no preview exists yet, offers to
// prepare one and polls the ledger until the pipeline attaches it.
export default function CleanPlayer({ entry }) {
  const [asset, setAsset] = useState(entry.preview_asset || null)
  const [url, setUrl] = useState(undefined) // undefined=loading, null=none
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

  // No preview yet: prepare one automatically (no button) and poll for it.
  useEffect(() => {
    if (asset || dispatched.current) return
    dispatched.current = true
    backend.submitPreview(entry).catch(() => {})
    poll.current = setInterval(async () => {
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
  }, [asset, entry])

  if (url) {
    return <video className="clean-player" controls preload="metadata" src={url} />
  }
  return (
    <div className="clean-player-prep">
      <p className="muted">{S.previewPreparing}</p>
    </div>
  )
}
