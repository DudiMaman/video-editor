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
  const [preparing, setPreparing] = useState(false)
  const [note, setNote] = useState('')
  const poll = useRef(null)

  useEffect(() => {
    if (!asset) {
      setUrl(null)
      return
    }
    let alive = true
    backend.scout
      .resolveOutput(asset)
      .then((u) => alive && setUrl(u ? u.videoUrl : null))
      .catch(() => alive && setUrl(null))
    return () => {
      alive = false
    }
  }, [asset])

  useEffect(() => () => clearInterval(poll.current), [])

  const prepare = async () => {
    setPreparing(true)
    setNote(S.previewPreparing)
    try {
      await backend.submitPreview(entry)
      poll.current = setInterval(async () => {
        try {
          const list = await backend.scout.readLedger()
          const e = list.find((x) => x.video_id === entry.video_id)
          if (e?.preview_asset) {
            clearInterval(poll.current)
            setPreparing(false)
            setNote('')
            setAsset(e.preview_asset)
          }
        } catch {
          /* keep polling */
        }
      }, 20000)
    } catch (e) {
      setPreparing(false)
      setNote(e.message)
    }
  }

  if (url) {
    return <video className="clean-player" controls preload="metadata" src={url} />
  }
  if (url === undefined) {
    return <p className="muted">…</p>
  }
  return (
    <div className="clean-player-prep">
      <button className="secondary" onClick={prepare} disabled={preparing}>
        {preparing ? S.previewPreparingBtn : S.preparePreview}
      </button>
      {note && <p className="muted">{note}</p>}
    </div>
  )
}
