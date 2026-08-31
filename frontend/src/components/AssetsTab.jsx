import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import OutroManager from './OutroManager.jsx'

const emptyForm = { name: '', description: '', link: '', hashtags: '' }

// The GitHub Actions secret expected to hold this venture's API key,
// per distributor (mirrors key_env_of in scripts/distribute_apps.py and
// scripts/buffer_wiring.py). Every BUFFER_TOKEN_* / ZERNIO_KEY_* secret
// is picked up by the workflows automatically - pasting the secret is
// the only setup step.
const idSlug = (asset) =>
  String(asset.id).replace(/[^A-Za-z0-9]/g, '_').toUpperCase()
const zernioSecretOf = (asset) => asset.zernioKeyEnv || `ZERNIO_KEY_${idSlug(asset)}`
const bufferSecretOf = (asset) => asset.bufferKeyEnv || `BUFFER_TOKEN_${idSlug(asset)}`

function AssetEditor({ asset, onSaved, onDeleted }) {
  const [form, setForm] = useState({
    name: asset.name,
    description: asset.description,
    link: asset.link,
    hashtags: asset.hashtags,
    distributor: asset.distributor || 'zernio',
    bufferAccountName: asset.bufferAccountName || '',
    bufferEmail: asset.bufferEmail || '',
    bufferLogin: asset.bufferLogin || '',
    zernioAccountId: asset.zernioAccountId || '',
    zernioAccountName: asset.zernioAccountName || '',
    zernioEmail: asset.zernioEmail || '',
    zernioLogin: asset.zernioLogin || '',
  })
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  const field = (key) => ({
    value: form[key],
    onChange: (e) => setForm({ ...form, [key]: e.target.value }),
  })

  const save = async () => {
    try {
      await backend.updateAsset(asset.id, form)
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
      await backend.deleteAsset(asset.id)
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
      <h4 style={{ margin: '14px 0 4px' }}>{STR.assets.distSection}</h4>
      <div className="fields">
        <label>
          {STR.assets.distributorLabel}
          <select value={form.distributor}
            onChange={(e) => setForm({ ...form, distributor: e.target.value })}>
            <option value="buffer">{STR.assets.distributorBuffer}</option>
            <option value="zernio">{STR.assets.distributorZernio}</option>
          </select>
        </label>
      </div>
      {form.distributor === 'buffer' ? (
        <>
          <p className="hint" style={{ margin: '8px 0' }}>
            {STR.assets.bufferKeyNote(bufferSecretOf(asset))}
          </p>
          <div className="fields">
            <label>
              {STR.assets.bufferAccountName}
              <input type="text" {...field('bufferAccountName')} />
            </label>
            <label>
              {STR.assets.bufferEmail}
              <input type="email" dir="ltr" {...field('bufferEmail')} />
            </label>
            <label>
              {STR.assets.bufferLogin}
              <select value={form.bufferLogin}
                onChange={(e) => setForm({ ...form, bufferLogin: e.target.value })}>
                <option value="">{STR.assets.zernioLoginNone}</option>
                <option value="google">{STR.assets.zernioLoginGoogle}</option>
                <option value="email">{STR.assets.zernioLoginEmail}</option>
              </select>
            </label>
            <label className="wide">
              {STR.assets.bufferChannelsLabel}
              <input type="text" dir="ltr" readOnly
                value={Object.entries(asset.bufferChannels || {})
                  .map(([p, id]) => `${p}: ${id}`).join(', ')}
                placeholder={STR.assets.bufferNoChannels} />
            </label>
          </div>
        </>
      ) : (
        <>
          <p className="hint" style={{ margin: '8px 0' }}>
            {STR.assets.zernioKeyNote(zernioSecretOf(asset))}
          </p>
          <div className="fields">
            <label>
              {STR.assets.zernioAccountName}
              <input type="text" {...field('zernioAccountName')} />
            </label>
            <label>
              {STR.assets.zernioEmail}
              <input type="email" dir="ltr" {...field('zernioEmail')} />
            </label>
            <label>
              {STR.assets.zernioLogin}
              <select value={form.zernioLogin}
                onChange={(e) => setForm({ ...form, zernioLogin: e.target.value })}>
                <option value="">{STR.assets.zernioLoginNone}</option>
                <option value="google">{STR.assets.zernioLoginGoogle}</option>
                <option value="email">{STR.assets.zernioLoginEmail}</option>
              </select>
            </label>
            <label className="wide">
              {STR.assets.zernioAccountId}
              <input type="text" dir="ltr" {...field('zernioAccountId')} />
            </label>
          </div>
        </>
      )}
      <div className="asset-actions">
        <button className="primary" onClick={save}>
          {saved ? STR.assets.saved : STR.assets.save}
        </button>
        <button className="danger" onClick={remove}>{STR.assets.delete}</button>
      </div>
      {error && <p className="error">{STR.errors.generic}{error}</p>}
      <OutroManager asset={asset} onChanged={onSaved} kind="outro" />
      {backend.supportsIntros && (
        <OutroManager asset={asset} onChanged={onSaved} kind="intro" />
      )}
    </div>
  )
}

export default function AssetsTab({ active }) {
  const [assets, setAssets] = useState([])
  const [form, setForm] = useState(emptyForm)
  const [error, setError] = useState('')

  const refresh = () =>
    backend.listAssets().then(setAssets).catch((e) => setError(e.message))

  useEffect(() => {
    if (active) refresh()
  }, [active])

  const create = async () => {
    if (!form.name.trim()) return
    try {
      await backend.createAsset(form)
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
