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

  // Registry writes can race each other (two quick uploads/deletes both
  // read the same assets.json sha; the second PUT then 409s). Re-read and
  // re-apply the mutation on conflict. A mutator returning false means
  // "nothing to change" and skips the write.
  async function mutateAssets(mutator, message) {
    for (let attempt = 0; ; attempt++) {
      const { assets, sha } = await readAssetsFile()
      if (mutator(assets) === false) return
      try {
        await writeAssetsFile(assets, sha, message)
        return
      } catch (e) {
        if (e.status !== 409 || attempt >= 2) throw e
      }
    }
  }

  const toClip = (p) => ({ id: p, original_name: p.split('/').pop() })

  const toUiAsset = (a) => ({
    id: a.id,
    name: a.name,
    description: a.description || '',
    link: a.link || '',
    hashtags: a.hashtags || '',
    outros: (a.outros || []).map(toClip),
    intros: (a.intros || []).map(toClip),
  })

  async function putFile(path, file, message) {
    const bytes = new Uint8Array(await file.arrayBuffer())
    await gh(`/contents/${encodePath(path)}`, {
      method: 'PUT',
      body: { message, content: bytesToB64(bytes), branch: 'main' },
    })
  }

  // kind is 'outros' or 'intros' — both the repo directory and the registry
  // key in assets.json.
  async function uploadClip(kind, assetId, file) {
    requireToken()
    const path = `${kind}/${assetId}/${Date.now()}-${safeFileName(file.name)}`
    await putFile(path, file, `Add ${kind} clip for ${assetId}`)
    await mutateAssets((assets) => {
      const a = assets.find((x) => String(x.id) === String(assetId))
      if (!a) throw new Error('asset not found')
      a[kind] = [...(a[kind] || []), path]
    }, `Register ${kind} clip for ${assetId}`)
  }

  async function deleteClip(kind, clip) {
    requireToken()
    const path = clip.id
    const parts = path.split('/')
    const name = parts.pop()
    const dir = parts.join('/')
    // Find the file's sha via the directory listing — robust for large
    // files, and lets us self-heal when the file is already gone.
    let entry = null
    try {
      const listing = await gh(`/contents/${encodePath(dir)}?ref=main`)
      entry = (Array.isArray(listing) ? listing : []).find((e) => e.name === name)
    } catch (e) {
      if (e.status !== 404) throw e
    }
    if (entry) {
      await gh(`/contents/${encodePath(path)}`, {
        method: 'DELETE',
        body: { message: `Delete ${kind} clip ${path}`, sha: entry.sha, branch: 'main' },
      })
    }
    // Unregister even when the file was already missing (ghost entry).
    await mutateAssets((assets) => {
      let changed = false
      for (const a of assets) {
        const before = (a[kind] || []).length
        a[kind] = (a[kind] || []).filter((p) => p !== path)
        if (a[kind].length !== before) changed = true
      }
      return changed ? undefined : false
    }, `Unregister ${kind} clip ${path}`)
  }

  // ---- Video Scout data files (data/*.json, data/brief.md) ----

  async function readRepoFile(path, fallback) {
    try {
      const res = await gh(`/contents/${encodePath(path)}?ref=main`)
      return { text: b64ToUtf8(res.content), sha: res.sha }
    } catch (e) {
      if (e.status === 404) return { text: fallback, sha: null }
      throw e
    }
  }

  async function writeRepoFile(path, text, sha, message) {
    const body = { message, content: utf8ToB64(text), branch: 'main' }
    if (sha) body.sha = sha
    await gh(`/contents/${encodePath(path)}`, { method: 'PUT', body })
  }

  // CAS loop like mutateAssets: re-read and re-apply on a 409 conflict.
  async function mutateDataJson(path, mutator, message, fallback = '[]') {
    requireToken()
    for (let attempt = 0; ; attempt++) {
      const { text, sha } = await readRepoFile(path, fallback)
      const data = JSON.parse(text || fallback)
      if (mutator(data) === false) return data
      try {
        await writeRepoFile(path, JSON.stringify(data, null, 1) + '\n', sha, message)
        return data
      } catch (e) {
        if (e.status !== 409 || attempt >= 2) throw e
      }
    }
  }

  const releaseByTagCache = {}
  async function releaseAssetUrls(tag, fileName) {
    if (!releaseByTagCache[tag]) {
      releaseByTagCache[tag] = gh(`/releases/tags/${encodeURIComponent(tag)}`).catch(() => null)
    }
    const rel = await releaseByTagCache[tag]
    const asset = (rel?.assets || []).find((a) => a.name === fileName)
    return asset ? { videoUrl: asset.browser_download_url, assetApiUrl: asset.url } : null
  }

  function toRequest(entry, rel) {
    const byName = Object.fromEntries(
      (rel.assets || []).map((a) => [a.name, a])
    )
    const req = entry.request || {}
    const asset = entry.video ? byName[entry.video] : null
    const videoUrl = asset ? asset.browser_download_url : null
    const hasIntro = !!req.intro
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
      start_seconds: req.start_seconds || 0,
      _videoUrl: videoUrl,
      _assetApiUrl: asset ? asset.url : null,
      _videoName: entry.video || null,
      _hasIntro: hasIntro,
    }
  }

  // Mirrors scripts/process_batch.py build_notes (without the mock warning).
  function buildNotes(entries, assetsById) {
    const lines = ['## תוצרים\n']
    for (const e of entries) {
      const req = e.request || {}
      const name = assetsById[String(req.asset)]?.name || req.asset
      const source = req.source_url || req.source_path || ''
      const cut = req.cut_seconds == null
        ? (req.start_seconds ? `מ-${req.start_seconds} שניות עד הסוף` : 'ללא חיתוך')
        : req.start_seconds
          ? `קטע ${req.start_seconds}–${req.cut_seconds} שניות`
          : `חיתוך בשניה ${req.cut_seconds}`
      if (e.status === 'done') {
        lines.push(`### ✅ ${name} — ${cut}`)
        lines.push(`מקור: \`${source}\` · קובץ: **${e.video}**\n`)
        if (e.caption) lines.push('```\n' + e.caption + '\n```\n')
        else if (e.caption_error) lines.push(`⚠️ יצירת הכיתוב נכשלה: ${e.caption_error}\n`)
      } else {
        lines.push(`### ❌ ${name} — ${cut}`)
        lines.push(`מקור: \`${source}\``)
        lines.push(`שגיאה: ${e.error}\n`)
      }
    }
    lines.push('<!-- machine-readable -->')
    lines.push('```json')
    lines.push(JSON.stringify(entries, null, 1))
    lines.push('```')
    return lines.join('\n')
  }

  // Render timestamps in the viewer's local timezone (Israel for our user).
  const fmtDate = (iso) => {
    if (!iso) return ''
    const d = new Date(iso)
    if (isNaN(d)) return iso
    return d.toLocaleString('he-IL', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  }

  return {
    mode: 'github',
    repo,
    supportsRecaption: false,
    supportsIntros: true,
    supportsScout: true,

    scout: {
      readInbox: async () => JSON.parse((await readRepoFile('data/inbox.json', '[]')).text || '[]'),
      mutateInbox: (mutator, message) => mutateDataJson('data/inbox.json', mutator, message),
      readLedger: async () => {
        const list = JSON.parse((await readRepoFile('data/ledger.json', '[]')).text || '[]')
        // Agents sometimes write "\n" as two literal characters; captions
        // must carry real newlines for display, copy, and publishing.
        for (const e of list) {
          if (e.caption && e.caption.includes('\\n')) {
            e.caption = e.caption.replace(/\\n/g, '\n')
          }
        }
        return list
      },
      mutateLedger: (mutator, message) => mutateDataJson('data/ledger.json', mutator, message),
      readSuggestions: async () =>
        JSON.parse((await readRepoFile('data/suggestions.json', '[]')).text || '[]'),
      mutateSuggestions: (mutator, message) =>
        mutateDataJson('data/suggestions.json', mutator, message),
      runDiscovery: async () => {
        requireToken()
        await gh('/actions/workflows/discover.yml/dispatches', {
          method: 'POST',
          body: { ref: 'main' },
        })
      },
      readConfig: async () =>
        JSON.parse((await readRepoFile('data/config.json', '{}')).text || '{}'),
      setDiscoveryDaily: (enabled) =>
        mutateDataJson(
          'data/config.json',
          (cfg) => {
            if (cfg.discovery_daily === enabled) return false
            cfg.discovery_daily = enabled
          },
          `Scout config: daily discovery ${enabled ? 'on' : 'off'}`,
          '{}'
        ),
      readBrief: () => readRepoFile('data/brief.md', ''),
      writeBrief: async (text, sha) => {
        requireToken()
        await writeRepoFile('data/brief.md', text, sha, 'Update scout brief')
      },
      // output_asset is "<release tag>/<file name>"
      resolveOutput: (outputAsset) => {
        const [tag, ...rest] = String(outputAsset || '').split('/')
        const file = rest.join('/')
        if (!tag || !file) return Promise.resolve(null)
        return releaseAssetUrls(tag, file)
      },
    },
    pollInterval: () => (token() ? 15000 : 60000),
    hasToken: () => !!token(),
    setToken: (t) => localStorage.setItem(TOKEN_KEY, t.trim()),
    clearToken: () => localStorage.removeItem(TOKEN_KEY),
    tokenCreateUrl: 'https://github.com/settings/personal-access-tokens/new',

    listAssets: async () => (await readAssetsFile()).assets.map(toUiAsset),

    createAsset: async (fields) => {
      requireToken()
      let id
      await mutateAssets((assets) => {
        id = slugify(fields.name)
        while (assets.some((a) => String(a.id) === id)) id = `${id}-2`
        assets.push({ id, ...fields, outros: [] })
      }, `Add asset: ${fields.name}`)
      return { id }
    },

    updateAsset: async (id, fields) => {
      requireToken()
      await mutateAssets((assets) => {
        const a = assets.find((x) => String(x.id) === String(id))
        if (!a) throw new Error('asset not found')
        Object.assign(a, fields)
      }, `Update asset: ${fields.name}`)
    },

    deleteAsset: async (id) => {
      requireToken()
      await mutateAssets((assets) => {
        const idx = assets.findIndex((a) => String(a.id) === String(id))
        if (idx === -1) return false
        assets.splice(idx, 1)
      }, `Delete asset: ${id}`)
    },

    uploadOutro: (assetId, file) => uploadClip('outros', assetId, file),
    uploadIntro: (assetId, file) => uploadClip('intros', assetId, file),
    deleteOutro: (outro) => deleteClip('outros', outro),
    deleteIntro: (intro) => deleteClip('intros', intro),
    outroUrl: (clip) => `https://raw.githubusercontent.com/${repo}/main/${clip.id}`,
    introUrl: (clip) => `https://raw.githubusercontent.com/${repo}/main/${clip.id}`,

    submitBatch: async (rows) => {
      requireToken()
      const requests = []
      for (const r of rows) {
        const item = {
          asset: r.assetId,
          outro: r.outroId,
        }
        const cut = parseFloat(r.cutSeconds)
        if (cut > 0) item.cut_seconds = cut
        if (r.introId) item.intro = r.introId
        const start = parseFloat(r.startSeconds)
        if (start > 0) item.start_seconds = start
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
        let entries = []
        const m = (rel.body || '').match(/```json\s*([\s\S]*?)```/)
        if (m) {
          try {
            entries = JSON.parse(m[1])
            requests = entries.map((e) => toRequest(e, rel))
          } catch { /* release without machine data */ }
        }
        batches.push({
          id: Number(rel.tag_name.replace('batch-', '')),
          created_at: fmtDate(rel.published_at),
          requests,
          _mockCaptions: /\[MOCK\]|מצב דמה/.test(rel.body || ''),
          _releaseId: rel.id,
          _entries: entries,
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

    // Write new captions into an existing batch release (body + json block).
    applyCaptions: async (batch, captionsByVideo) => {
      requireToken()
      const entries = batch._entries || []
      for (const e of entries) {
        if (e.video && captionsByVideo[e.video]) {
          e.caption = captionsByVideo[e.video]
          e.caption_error = null
        }
      }
      const { assets } = await readAssetsFile()
      const byId = Object.fromEntries(assets.map((a) => [String(a.id), a]))
      await gh(`/releases/${batch._releaseId}`, {
        method: 'PATCH',
        body: { body: buildNotes(entries, byId) },
      })
    },

    // Fetch the finished video as a Blob for the Web Share API. The plain
    // download URL usually lacks CORS headers, so fall back to the GitHub API
    // asset endpoint with an octet-stream Accept header.
    fetchVideoBlob: async (r) => {
      try {
        const res = await fetch(r._videoUrl)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return await res.blob()
      } catch {
        if (!r._assetApiUrl) throw new Error('no asset url')
        const headers = { Accept: 'application/octet-stream' }
        if (token()) headers.Authorization = `Bearer ${token()}`
        const res = await fetch(r._assetApiUrl, { headers })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return await res.blob()
      }
    },
  }
}
