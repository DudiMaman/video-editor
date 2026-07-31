// Adapter for GitHub-hosted mode (GitHub Pages UI + Actions processing).
//
// Reads are unauthenticated (public repo). Writes — submitting batches,
// managing assets, uploading outros — go through the GitHub API and require
// a fine-grained personal access token with Contents:RW + Actions:RW,
// stored in localStorage.
import { STR } from '../strings.js'

const TOKEN_KEY = 'video_editor_gh_token'

function detectRepo(params) {
  const explicit = params.get('repo')
  if (explicit) return explicit
  const owner = location.hostname.split('.')[0]
  const seg = location.pathname.split('/').filter(Boolean)[0]
  return `${owner}/${seg || 'video-editor'}`
}

function bytesToB64(bytes) {
  let bin = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
  }
  return btoa(bin)
}

const utf8ToB64 = (str) => bytesToB64(new TextEncoder().encode(str))

function b64ToUtf8(b64) {
  const bin = atob(b64.replace(/\s/g, ''))
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)))
}

function slugify(name) {
  const s = String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return s || `app-${Date.now()}`
}

function safeFileName(name) {
  const cleaned = String(name || 'video.mp4').replace(/[^\w.-]+/g, '-')
  return /\.\w+$/.test(cleaned) ? cleaned : cleaned + '.mp4'
}

const encodePath = (p) => p.split('/').map(encodeURIComponent).join('/')

export function create(params) {
  const repo = detectRepo(params)
  const api = `https://api.github.com/repos/${repo}`
  const token = () => localStorage.getItem(TOKEN_KEY) || ''

  async function gh(path, { method = 'GET', body, raw = false } = {}) {
    const headers = {
      Accept: raw ? 'application/vnd.github.raw+json' : 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    }
    if (token()) headers.Authorization = `Bearer ${token()}`
    if (body) headers['Content-Type'] = 'application/json'
    const res = await fetch(api + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) {
      let msg = `HTTP ${res.status}`
      try {
        msg = (await res.json()).message || msg
      } catch { /* keep status text */ }
      if (method !== 'GET' && [401, 403, 404].includes(res.status)) {
        msg = `${STR.github.needToken} (${msg})`
      }
      const err = new Error(msg)
      err.status = res.status
      throw err
    }
    if (res.status === 204) return null
    const text = await res.text()
    return text ? (raw ? text : JSON.parse(text)) : null
  }

  function requireToken() {
    if (!token()) throw new Error(STR.github.needToken)
  }

  async function readAssetsFile() {
    try {
      const res = await gh('/contents/assets.json?ref=main')
      return { assets: JSON.parse(b64ToUtf8(res.content)), sha: res.sha }
    } catch (e) {
      if (e.status === 404) return { assets: [], sha: null }
      throw e
    }
  }

  async function writeAssetsFile(assets, sha, message) {
    const body = {
      message,
      content: utf8ToB64(JSON.stringify(assets, null, 1) + '\n'),
      branch: 'main',
    }
    if (sha) body.sha = sha
    await gh('/contents/assets.json', { method: 'PUT', body })
  }

  const toUiAsset = (a) => ({
    id: a.id,
    name: a.name,
    description: a.description || '',
    link: a.link || '',
    hashtags: a.hashtags || '',
    outros: (a.outros || []).map((p) => ({ id: p, original_name: p.split('/').pop() })),
  })

  async function putFile(path, file, message) {
    const bytes = new Uint8Array(await file.arrayBuffer())
    await gh(`/contents/${encodePath(path)}`, {
      method: 'PUT',
      body: { message, content: bytesToB64(bytes), branch: 'main' },
    })
  }

  function toRequest(entry, rel) {
    const byName = Object.fromEntries(
      (rel.assets || []).map((a) => [a.name, a.browser_download_url])
    )
    const req = entry.request || {}
    const videoUrl = entry.video ? byName[entry.video] : null
    return {
      id: `${rel.tag_name}-${entry.index}`,
      asset_id: req.asset ?? null,
      cut_seconds: req.cut_seconds ?? null,
      source_url: req.source_url || req.source_path || null,
      status: entry.status === 'done' ? 'done' : 'failed',
      error:
        entry.error ||
        (entry.caption_error ? `${STR.github.captionFailed}: ${entry.caption_error}` : null),
      caption: entry.caption,
      has_output: !!videoUrl,
      _videoUrl: videoUrl,
    }
  }

  const fmtDate = (iso) => (iso ? iso.replace('T', ' ').replace(/Z|\.\d+.*$/, '') : '')

  return {
    mode: 'github',
    repo,
    supportsRecaption: false,
    pollInterval: () => (token() ? 15000 : 60000),
    hasToken: () => !!token(),
    setToken: (t) => localStorage.setItem(TOKEN_KEY, t.trim()),
    clearToken: () => localStorage.removeItem(TOKEN_KEY),
    tokenCreateUrl: 'https://github.com/settings/personal-access-tokens/new',

    listAssets: async () => (await readAssetsFile()).assets.map(toUiAsset),

    createAsset: async (fields) => {
      requireToken()
      const { assets, sha } = await readAssetsFile()
      let id = slugify(fields.name)
      while (assets.some((a) => String(a.id) === id)) id = `${id}-2`
      assets.push({ id, ...fields, outros: [] })
      await writeAssetsFile(assets, sha, `Add asset: ${fields.name}`)
      return { id }
    },

    updateAsset: async (id, fields) => {
      requireToken()
      const { assets, sha } = await readAssetsFile()
      const a = assets.find((x) => String(x.id) === String(id))
      if (!a) throw new Error('asset not found')
      Object.assign(a, fields)
      await writeAssetsFile(assets, sha, `Update asset: ${fields.name}`)
    },

    deleteAsset: async (id) => {
      requireToken()
      const { assets, sha } = await readAssetsFile()
      await writeAssetsFile(
        assets.filter((a) => String(a.id) !== String(id)),
        sha,
        `Delete asset: ${id}`
      )
    },

    uploadOutro: async (assetId, file) => {
      requireToken()
      const path = `outros/${assetId}/${Date.now()}-${safeFileName(file.name)}`
      await putFile(path, file, `Add outro for ${assetId}`)
      const { assets, sha } = await readAssetsFile()
      const a = assets.find((x) => String(x.id) === String(assetId))
      if (!a) throw new Error('asset not found')
      a.outros = [...(a.outros || []), path]
      await writeAssetsFile(assets, sha, `Register outro for ${assetId}`)
    },

    deleteOutro: async (outro) => {
      requireToken()
      const path = outro.id
      const info = await gh(`/contents/${encodePath(path)}?ref=main`)
      await gh(`/contents/${encodePath(path)}`, {
        method: 'DELETE',
        body: { message: `Delete outro ${path}`, sha: info.sha, branch: 'main' },
      })
      const { assets, sha } = await readAssetsFile()
      for (const a of assets) {
        a.outros = (a.outros || []).filter((p) => p !== path)
      }
      await writeAssetsFile(assets, sha, `Unregister outro ${path}`)
    },

    outroUrl: (outro) => `https://raw.githubusercontent.com/${repo}/main/${outro.id}`,

    submitBatch: async (rows) => {
      requireToken()
      const requests = []
      for (const r of rows) {
        const item = {
          asset: r.assetId,
          outro: r.outroId,
          cut_seconds: parseFloat(r.cutSeconds),
        }
        if (r.sourceType === 'url') {
          item.source_url = r.url.trim()
        } else {
          const path = `inbox/${Date.now()}-${safeFileName(r.file.name)}`
          await putFile(path, r.file, 'Upload source video')
          item.source_path = path
        }
        requests.push(item)
      }
      await gh('/actions/workflows/process.yml/dispatches', {
        method: 'POST',
        body: { ref: 'main', inputs: { requests: JSON.stringify(requests) } },
      })
    },

    listBatches: async () => {
      const [releases, runsRes] = await Promise.all([
        gh('/releases?per_page=15'),
        gh('/actions/workflows/process.yml/runs?per_page=10').catch(() => null),
      ])
      const batches = []
      for (const rel of (releases || []).filter((r) => r.tag_name.startsWith('batch-'))) {
        let requests = []
        const m = (rel.body || '').match(/```json\s*([\s\S]*?)```/)
        if (m) {
          try {
            requests = JSON.parse(m[1]).map((e) => toRequest(e, rel))
          } catch { /* release without machine data */ }
        }
        batches.push({
          id: Number(rel.tag_name.replace('batch-', '')),
          created_at: fmtDate(rel.published_at),
          requests,
        })
      }
      const seen = new Set(batches.map((b) => String(b.id)))
      for (const run of runsRes?.workflow_runs || []) {
        if (seen.has(String(run.run_number))) continue
        if (run.status === 'completed' && run.conclusion === 'success') continue
        const status =
          run.status === 'completed' ? 'failed' : run.status === 'queued' ? 'queued' : 'processing'
        batches.push({
          id: run.run_number,
          created_at: fmtDate(run.created_at),
          requests: [{
            id: `run-${run.id}`,
            asset_id: null,
            cut_seconds: null,
            source_url: null,
            status,
            error: status === 'failed' ? STR.github.runFailed : null,
            caption: null,
            has_output: false,
            _runUrl: run.html_url,
          }],
        })
      }
      batches.sort((a, b) => b.id - a.id)
      return batches
    },

    videoUrl: (r) => r._videoUrl,
    downloadUrl: (r) => r._videoUrl,
    recaption: () => Promise.reject(new Error('not supported')),
  }
}
