import { useRef, useState } from 'react'
import { STR } from '../strings.js'
import { postForm, del } from '../api.js'

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
      const fd = new FormData()
      fd.append('file', file)
      await postForm(`/api/assets/${asset.id}/outros`, fd)
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const remove = async (outroId) => {
    if (!confirm(STR.assets.confirmDeleteOutro)) return
    try {
      await del(`/api/outros/${outroId}`)
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
            <video controls preload="metadata" src={`/api/outros/${o.id}/file`} />
            <div className="outro-meta">
              <span dir="ltr">{o.original_name}</span>
              <button className="danger small" onClick={() => remove(o.id)}>
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
