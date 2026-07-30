import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { get, post, put, del } from '../api.js'
import OutroManager from './OutroManager.jsx'

const emptyForm = { name: '', description: '', link: '', hashtags: '' }

function AssetEditor({ asset, onSaved, onDeleted }) {
  const [form, setForm] = useState({
    name: asset.name,
    description: asset.description,
    link: asset.link,
    hashtags: asset.hashtags,
  })
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  const field = (key) => ({
    value: form[key],
    onChange: (e) => setForm({ ...form, [key]: e.target.value }),
  })

  const save = async () => {
    try {
      await put(`/api/assets/${asset.id}`, form)
      setSaved(true)
      setError('')
      setTimeout(() => setSaved(false), 1500)
      onSaved()
    } catch (e) {
      setError(e.message)
    }
  }

  const remove = async () => {
    if (!confirm(STR.assets.confirmDelete)) return
    try {
      await del(`/api/assets/${asset.id}`)
      onDeleted()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="card asset-card">
      <div className="fields">
        <label>
          {STR.assets.name}
          <input type="text" {...field('name')} />
        </label>
        <label>
          {STR.assets.link}
          <input type="url" dir="ltr" {...field('link')} />
        </label>
        <label className="wide">
          {STR.assets.description}
          <textarea rows={2} {...field('description')} />
        </label>
        <label>
          {STR.assets.hashtags}
          <input type="text" dir="ltr" {...field('hashtags')} />
        </label>
      </div>
      <div className="asset-actions">
        <button className="primary" onClick={save}>
          {saved ? STR.assets.saved : STR.assets.save}
        </button>
        <button className="danger" onClick={remove}>{STR.assets.delete}</button>
      </div>
      {error && <p className="error">{STR.errors.generic}{error}</p>}
      <OutroManager asset={asset} onChanged={onSaved} />
    </div>
  )
}

export default function AssetsTab({ active }) {
  const [assets, setAssets] = useState([])
  const [form, setForm] = useState(emptyForm)
  const [error, setError] = useState('')

  const refresh = () =>
    get('/api/assets').then(setAssets).catch((e) => setError(e.message))

  useEffect(() => {
    if (active) refresh()
  }, [active])

  const create = async () => {
    if (!form.name.trim()) return
    try {
      await post('/api/assets', form)
      setForm(emptyForm)
      setError('')
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="assets-tab">
      <h2>{STR.assets.title}</h2>
      {assets.length === 0 && <p className="empty">{STR.assets.empty}</p>}
      {assets.map((a) => (
        <AssetEditor key={a.id} asset={a} onSaved={refresh} onDeleted={refresh} />
      ))}

      <div className="card new-asset">
        <h3>{STR.assets.create}</h3>
        <div className="fields">
          <label>
            {STR.assets.name}
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            {STR.assets.link}
            <input
              type="url"
              dir="ltr"
              value={form.link}
              onChange={(e) => setForm({ ...form, link: e.target.value })}
            />
          </label>
          <label className="wide">
            {STR.assets.description}
            <textarea
              rows={2}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </label>
          <label>
            {STR.assets.hashtags}
            <input
              type="text"
              dir="ltr"
              value={form.hashtags}
              onChange={(e) => setForm({ ...form, hashtags: e.target.value })}
            />
          </label>
        </div>
        <button className="primary" onClick={create} disabled={!form.name.trim()}>
          {STR.assets.create}
        </button>
        {error && <p className="error">{STR.errors.generic}{error}</p>}
      </div>
    </div>
  )
}
