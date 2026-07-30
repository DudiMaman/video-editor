import { useRef, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

export default function OutroManager({ asset, onChanged }) {
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef(null)

  const upload = async (e) => {
    const file = e.target.files[0]
    if (!file) return
    setUploading(true)
    setError('')
    try {
      await backend.uploadOutro(asset.id, file)
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const remove = async (outro) => {
    if (!confirm(STR.assets.confirmDeleteOutro)) return
    try {
      await backend.deleteOutro(outro)
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="outro-manager">
      <h4>{STR.assets.outros}</h4>
      {asset.outros.length === 0 && <p className="muted">{STR.assets.noOutros}</p>}
      <div className="outro-list">
        {asset.outros.map((o) => (
          <div key={o.id} className="outro-item">
            <video controls preload="metadata" src={backend.outroUrl(o)} />
            <div className="outro-meta">
              <span dir="ltr">{o.original_name}</span>
              <button className="danger small" onClick={() => remove(o)}>
                {STR.assets.deleteOutro}
              </button>
            </div>
          </div>
        ))}
      </div>
      <label className="upload-label">
        {uploading ? '...' : STR.assets.uploadOutro}
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
