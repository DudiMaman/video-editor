import { useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import ClipPicker from './ClipPicker.jsx'

export default function RequestRow({ row, assets, onChange, onRemove, onAssetsChanged }) {
  const [openPicker, setOpenPicker] = useState(null) // 'outro' | 'intro' | null
  const asset = assets.find((a) => String(a.id) === String(row.assetId))
  const outros = asset ? asset.outros : []
  const intros = (asset && asset.intros) || []

  const pickAsset = (assetId) => {
    const a = assets.find((x) => String(x.id) === String(assetId))
    const onlyOutro = a && a.outros.length === 1 ? String(a.outros[0].id) : ''
    const onlyIntro =
      a && (a.intros || []).length === 1 ? String(a.intros[0].id) : ''
    setOpenPicker(null)
    onChange({ assetId, outroId: onlyOutro, introId: onlyIntro })
  }

  const clipName = (clips, id) =>
    clips.find((c) => String(c.id) === String(id))?.original_name

  const togglePicker = (kind) =>
    setOpenPicker((cur) => (cur === kind ? null : kind))

  const uploadFromPicker = async (kind, file) => {
    if (kind === 'intro') await backend.uploadIntro(asset.id, file)
    else await backend.uploadOutro(asset.id, file)
    await onAssetsChanged()
  }

  return (
    <div className="request-row card">
      <div className="fields">
        <label>
          {STR.batch.asset}
          <select value={row.assetId} onChange={(e) => pickAsset(e.target.value)}>
            <option value="">{STR.batch.selectAsset}</option>
            {assets.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
        </label>

        <div className="picker-field">
          <span className="picker-label">{STR.batch.outro}</span>
          <button
            className={'picker-trigger' + (openPicker === 'outro' ? ' active' : '')}
            onClick={() => togglePicker('outro')}
            disabled={!asset}
          >
            {clipName(outros, row.outroId) || STR.batch.selectOutro} ▾
          </button>
        </div>

        {backend.supportsIntros && (
          <div className="picker-field">
            <span className="picker-label">{STR.batch.intro}</span>
            <button
              className={'picker-trigger' + (openPicker === 'intro' ? ' active' : '')}
              onClick={() => togglePicker('intro')}
              disabled={!asset}
            >
              {clipName(intros, row.introId) || STR.batch.noIntro} ▾
            </button>
          </div>
        )}

        <div className="source-field">
          <div className="source-toggle">
            <button
              className={row.sourceType === 'upload' ? 'active' : ''}
              onClick={() => onChange({ sourceType: 'upload' })}
            >
              {STR.batch.sourceUpload}
            </button>
            <button
              className={row.sourceType === 'url' ? 'active' : ''}
              onClick={() => onChange({ sourceType: 'url' })}
            >
              {STR.batch.sourceUrl}
            </button>
          </div>
          {row.sourceType === 'upload' ? (
            <input
              type="file"
              accept="video/*"
              onChange={(e) => onChange({ file: e.target.files[0] || null })}
            />
          ) : (
            <input
              type="url"
              dir="ltr"
              placeholder={STR.batch.urlPlaceholder}
              value={row.url}
              onChange={(e) => onChange({ url: e.target.value })}
            />
          )}
        </div>

        <label>
          {STR.batch.startSeconds}
          <input
            type="number"
            dir="ltr"
            min="0"
            step="0.1"
            placeholder="0"
            value={row.startSeconds}
            onChange={(e) => onChange({ startSeconds: e.target.value })}
          />
        </label>

        <label>
          {STR.batch.cutSeconds}
          <input
            type="number"
            dir="ltr"
            min="0.1"
            step="0.1"
            value={row.cutSeconds}
            onChange={(e) => onChange({ cutSeconds: e.target.value })}
          />
        </label>
      </div>

      {openPicker === 'outro' && asset && (
        <ClipPicker
          title={STR.batch.pickOutroTitle}
          clips={outros}
          value={row.outroId}
          urlFor={(c) => backend.outroUrl(c)}
          onPick={(id) => {
            onChange({ outroId: id })
            setOpenPicker(null)
          }}
          onUpload={(file) => uploadFromPicker('outro', file)}
        />
      )}
      {openPicker === 'intro' && asset && (
        <ClipPicker
          title={STR.batch.pickIntroTitle}
          clips={intros}
          value={row.introId || ''}
          allowNone
          noneLabel={STR.batch.noIntro}
          urlFor={(c) => backend.introUrl(c)}
          onPick={(id) => {
            onChange({ introId: id })
            setOpenPicker(null)
          }}
          onUpload={(file) => uploadFromPicker('intro', file)}
        />
      )}

      {asset && outros.length === 0 && (
        <p className="warning">{STR.batch.noOutros}</p>
      )}
      <p className="hint">{STR.batch.cutHint}</p>
      {onRemove && (
        <button className="remove" onClick={onRemove}>
          {STR.batch.removeRow}
        </button>
      )}
    </div>
  )
}
