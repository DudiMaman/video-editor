// Adapter for the local FastAPI server (/api/*).
import { get, post, put, del, postForm } from '../api.js'

export function create() {
  return {
    mode: 'local',
    supportsRecaption: true,
    supportsIntros: false,
    pollInterval: () => 2500,
    hasToken: () => true,
    setToken: () => {},
    clearToken: () => {},

    listAssets: () => get('/api/assets'),
    createAsset: (fields) => post('/api/assets', fields),
    updateAsset: (id, fields) => put(`/api/assets/${id}`, fields),
    deleteAsset: (id) => del(`/api/assets/${id}`),

    uploadOutro: (assetId, file) => {
      const fd = new FormData()
      fd.append('file', file)
      return postForm(`/api/assets/${assetId}/outros`, fd)
    },
    deleteOutro: (outro) => del(`/api/outros/${outro.id}`),
    outroUrl: (outro) => `/api/outros/${outro.id}/file`,

    submitBatch: (rows) => {
      const fd = new FormData()
      const payload = rows.map((r, i) => ({
        asset_id: Number(r.assetId),
        outro_id: Number(r.outroId),
        cut_seconds: parseFloat(r.cutSeconds),
        source_type: r.sourceType,
        ...(r.sourceType === 'url'
          ? { source_url: r.url.trim() }
          : { file_key: `file_${i}` }),
      }))
      rows.forEach((r, i) => {
        if (r.sourceType === 'upload') fd.append(`file_${i}`, r.file)
      })
      fd.append('requests', JSON.stringify(payload))
      return postForm('/api/batches', fd)
    },

    listBatches: () => get('/api/batches'),
    videoUrl: (r) => `/api/requests/${r.id}/video`,
    downloadUrl: (r) => `/api/requests/${r.id}/video?download=1`,
    recaption: (r) => post(`/api/requests/${r.id}/recaption`),

    fetchVideoBlob: async (r) => {
      const res = await fetch(`/api/requests/${r.id}/video`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.blob()
    },
  }
}
