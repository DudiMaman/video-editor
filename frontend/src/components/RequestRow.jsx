import { STR } from '../strings.js'

export default function RequestRow({ row, assets, onChange, onRemove }) {
  const asset = assets.find((a) => String(a.id) === String(row.assetId))
  const outros = asset ? asset.outros : []

  const pickAsset = (assetId) => {
    const a = assets.find((x) => String(x.id) === String(assetId))
    const onlyOutro = a && a.outros.length === 1 ? String(a.outros[0].id) : ''
    onChange({ assetId, outroId: onlyOutro })
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

        <label>
          {STR.batch.outro}
          <select
            value={row.outroId}
            onChange={(e) => onChange({ outroId: e.target.value })}
            disabled={!asset}
          >
            <option value="">{STR.batch.selectOutro}</option>
            {outros.map((o) => (
              <option key={o.id} value={o.id}>{o.original_name}</option>
            ))}
          </select>
        </label>

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
