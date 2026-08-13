import { useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'
import ClipPicker from './ClipPicker.jsx'

export default function RequestRow({ row, assets, onChange, onRemove, onAssetsChanged }) {
  const [openPicker, setOpenPicker] = useState(null) // 'outro' | 'intro' | null
  const [subBusy, setSubBusy] = useState(false)
  const [subNote, setSubNote] = useState('')

  const flashNote = (msg, ms = 4000) => {
    setSubNote(msg)
    setTimeout(() => setSubNote(''), ms)
  }

  const transcribe = async () => {
    setSubBusy(true)
    try {
      const key = await backend.submitTranscribe(row)
      onChange({ transcriptKey: key })
      flashNote(STR.batch.transcribeSent, 8000)
    } catch (e) {
      alert(e.message)
    } finally {
      setSubBusy(false)
    }
  }

  const loadTranscript = async () => {
    setSubBusy(true)
    try {
      const source =
        row.transcriptKey ||
        (row.sourceType === 'url' ? row.url.trim() : row.file?.name)
      const t = await backend.readTranscript(source)
      if (!t || !t.cues?.length) {
        flashNote(STR.batch.transcriptMissing)
      } else {
        onChange({
          subtitlesText: t.cues
            .map((c) => `${c.start} - ${c.end} | ${c.text}`)
            .join('\n'),
        })
        flashNote(STR.batch.transcriptLoaded)
      }
    } catch (e) {
      alert(e.message)
    } finally {
      setSubBusy(false)
    }
  }
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
              className={row.sourceType === 'url' ? 'active' : ''}
              onClick={() => onChange({ sourceType: 'url' })}
            >
              {STR.batch.sourceUrl}
            </button>
            <button
              className={row.sourceType === 'upload' ? 'active' : ''}
              onClick={() => onChange({ sourceType: 'upload' })}
            >
              {STR.batch.sourceUpload}
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

      {backend.submitTranscribe && (
        <div className="subtitles-field">
          <span className="picker-label">{STR.batch.subtitlesLabel}</span>
          <div className="result-actions">
            <button
              className="secondary small"
              disabled={subBusy || (row.sourceType === 'upload' ? !row.file : !row.url.trim())}
              onClick={transcribe}
            >
              {STR.batch.transcribeBtn}
            </button>
            <button
              className="secondary small"
              disabled={subBusy || (row.sourceType === 'upload' ? !row.file : !row.url.trim())}
              onClick={loadTranscript}
            >
              {STR.batch.loadTranscriptBtn}
            </button>
            {subNote && <span className="ok">{subNote}</span>}
          </div>
          <textarea
            dir="ltr"
            rows={6}
            placeholder={STR.batch.subtitlesPlaceholder}
            value={row.subtitlesText || ''}
            onChange={(e) => onChange({ subtitlesText: e.target.value })}
          />
          <p className="hint">{STR.batch.subtitlesHint}</p>
        </div>
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
