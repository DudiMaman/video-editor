import { useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const PLATFORMS = [
  { key: 'tiktok', label: 'TikTok', url: 'https://www.tiktok.com/tiktokstudio/upload' },
  { key: 'youtube', label: 'YouTube', url: 'https://www.youtube.com/upload' },
  { key: 'instagram', label: 'Instagram', url: 'https://www.instagram.com/' },
  { key: 'facebook', label: 'Facebook', url: 'https://www.facebook.com/' },
]

export default function ShareBar({ request }) {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const r = request

  const flash = (text) => {
    setMessage(text)
    setTimeout(() => setMessage(''), 6000)
  }

  // Native share sheet (mobile): hands the actual video file + caption to
  // the OS, which offers TikTok/Instagram/etc. directly.
  const nativeShare = async () => {
    if (!navigator.share) {
      flash(STR.share.unsupported)
      return
    }
    setBusy(true)
    try {
      const blob = await backend.fetchVideoBlob(r)
      const file = new File([blob], 'video.mp4', { type: 'video/mp4' })
      if (navigator.canShare && !navigator.canShare({ files: [file] })) {
        flash(STR.share.unsupported)
        return
      }
      await navigator.share({ files: [file], text: r.caption || undefined })
    } catch (e) {
      if (e.name !== 'AbortError') flash(STR.share.unsupported)
    } finally {
      setBusy(false)
    }
  }

  // Desktop flow: copy the caption, start the video download, and open the
  // platform's upload page — the user drags the file in and pastes.
  const openPlatform = async (p) => {
    if (r.caption) {
      try {
        await navigator.clipboard.writeText(r.caption)
      } catch { /* clipboard may be blocked; caption is still visible below */ }
    }
    // Open the platform tab first — window.open needs the click's user
    // activation; the attachment-disposition URL then downloads without
    // navigating away.
    window.open(p.url, '_blank', 'noopener')
    window.location.assign(backend.downloadUrl(r))
    flash(r.caption ? STR.share.openedWithCaption : STR.share.opened)
  }

  return (
    <div className="share-bar">
      <div className="share-buttons">
        <button className="primary" onClick={nativeShare} disabled={busy}>
          {busy ? '...' : STR.share.share}
        </button>
        <span className="muted">{STR.share.uploadTo}</span>
        {PLATFORMS.map((p) => (
          <button key={p.key} className="secondary small" onClick={() => openPlatform(p)}>
            {p.label}
          </button>
        ))}
      </div>
      {message && <p className="hint share-hint">{message}</p>}
    </div>
  )
}
