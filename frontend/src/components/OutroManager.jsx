import { useRef, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

// Manages one clip library (outros or intros) for an asset.
export default function OutroManager({ asset, onChanged, kind = 'outro' }) {
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef(null)

  const isIntro = kind === 'intro'
  const clips = (isIntro ? asset.intros : asset.outros) || []
  const S = isIntro ? STR.intros : STR.assets

  const upload = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setUploading(true)
    setError('')
    try {
      if (isIntro) await backend.uploadIntro(asset.id, file)
      else await backend.uploadOutro(asset.id, file)
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const remove = async (clip) => {
    if (!confirm(S.confirmDeleteClip)) return
    try {
      if (isIntro) await backend.deleteIntro(clip)
      else await backend.deleteOutro(clip)
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  const clipUrl = (clip) =>
    isIntro ? backend.introUrl(clip) : backend.outroUrl(clip)

  return (
    <div className="outro-manager">
      <h4>{S.clipsTitle}</h4>
      {clips.length === 0 && <p className="muted">{S.noClips}</p>}
      <div className="outro-list">
        {clips.map((o) => (
          <div key={o.id} className="outro-item">
            <video controls preload="metadata" src={clipUrl(o)} />
            <div className="outro-meta">
              <span dir="ltr">{o.original_name}</span>
              <button className="danger small" onClick={() => remove(o)}>
                {S.deleteClip}
              </button>
            </div>
          </div>
        ))}
      </div>
      <label className="upload-label">
        {uploading ? '...' : S.uploadClip}
        <input
          ref={fileInput}
          type="file"
          accept="video/*"
          onChange={upload}
          disabled={uploading}
          hidden
        />
      </label>
      {error && <p className="error">{STR.errors.generic}{error}</p>}
    </div>
  )
}
