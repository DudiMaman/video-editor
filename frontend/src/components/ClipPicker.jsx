import { useRef, useState } from 'react'
import { STR } from '../strings.js'

// Visual clip chooser: playable video previews, optional "none" card,
// and optional inline upload.
export default function ClipPicker({
  title,
  clips,
  value,
  allowNone,
  noneLabel,
  urlFor,
  onPick,
  onUpload,
}) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const fileInput = useRef(null)

  const upload = async (e) => {
    const file = e.target.files[0]
    if (!file || !onUpload) return
    setUploading(true)
    setError('')
    try {
      await onUpload(file)
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  return (
    <div className="clip-picker card">
      <h4>{title}</h4>
      <div className="clip-grid">
        {allowNone && (
          <div
            className={'clip-card clip-none' + (!value ? ' selected' : '')}
            onClick={() => onPick('')}
          >
            <span>{noneLabel}</span>
          </div>
        )}
        {clips.map((c) => (
          <div
            key={c.id}
            className={
              'clip-card' + (String(value) === String(c.id) ? ' selected' : '')
            }
          >
            <video controls preload="metadata" src={urlFor(c)} />
            <button className="primary small" onClick={() => onPick(String(c.id))}>
              {STR.batch.choose}
            </button>
            <span className="muted clip-name" dir="ltr">{c.original_name}</span>
          </div>
        ))}
      </div>
      {onUpload && (
        <label className="upload-label">
          {uploading ? '...' : STR.batch.uploadNewClip}
          <input
            ref={fileInput}
            type="file"
            accept="video/*"
            onChange={upload}
            disabled={uploading}
            hidden
          />
        </label>
      )}
      {error && <p className="error">{STR.errors.generic}{error}</p>}
    </div>
  )
}
