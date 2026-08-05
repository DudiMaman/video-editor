// In-browser caption generation: extract frames from the finished video,
// send them to the Claude API with the asset details, and write the new
// captions back into the batch release. Runs entirely client-side with the
// Anthropic key from localStorage — no repository secret involved.
import { STR } from './strings.js'

const KEY_STORAGE = 'video_editor_anthropic_key'
const CLAUDE_MODEL = 'claude-sonnet-5'

export const getAnthropicKey = () => localStorage.getItem(KEY_STORAGE) || ''
export const setAnthropicKey = (k) => localStorage.setItem(KEY_STORAGE, k.trim())
export const clearAnthropicKey = () => localStorage.removeItem(KEY_STORAGE)

const SYSTEM_PROMPT =
  'You are a social-media marketing copywriter specializing in short-form ' +
  'video captions that drive mobile app downloads. You write in English. ' +
  'You will be shown frames from a video that promotes a mobile app, plus ' +
  'details about the app. Write ONE caption for posting alongside the video. ' +
  'Requirements: (1) open with a scroll-stopping hook grounded in what is ' +
  'actually visible in the frames - reference the real content, not generic ' +
  'hype; (2) 1-3 short punchy sentences plus the CTA; (3) include a clear ' +
  'call-to-action to download/try the app; (4) include the app link exactly ' +
  'as given; (5) end with the provided hashtags; (6) use at most 2-3 fitting ' +
  'emojis; (7) apply short-form best practices - curiosity gap, urgency or ' +
  'social proof where natural, line breaks for scannability. Return ONLY ' +
  'the caption text - no preamble, no quotes, no explanations.'

async function extractFrames(blob, sampleWindow, introOffset) {
  const url = URL.createObjectURL(blob)
  try {
    const video = document.createElement('video')
    video.muted = true
    video.src = url
    await new Promise((res, rej) => {
      video.onloadedmetadata = res
      video.onerror = () => rej(new Error('video load failed'))
    })
    const duration = video.duration || sampleWindow || 10
    const start = Math.min(introOffset, Math.max(0, duration - 1))
    const end = Math.min(start + (sampleWindow || duration), duration)
    const span = Math.max(1, end - start)
    const canvas = document.createElement('canvas')
    const scale = Math.min(1, 512 / video.videoWidth)
    canvas.width = Math.round(video.videoWidth * scale)
    canvas.height = Math.round(video.videoHeight * scale)
    const ctx = canvas.getContext('2d')
    const frames = []
    for (const frac of [0.15, 0.5, 0.85]) {
      video.currentTime = start + span * frac
      await new Promise((res, rej) => {
        video.onseeked = res
        video.onerror = () => rej(new Error('video seek failed'))
      })
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
      const dataUrl = canvas.toDataURL('image/jpeg', 0.7)
      frames.push(dataUrl.split(',')[1])
    }
    return frames
  } finally {
    URL.revokeObjectURL(url)
  }
}

async function callClaude(frames, asset) {
  const content = frames.map((data) => ({
    type: 'image',
    source: { type: 'base64', media_type: 'image/jpeg', data },
  }))
  content.push({
    type: 'text',
    text:
      'These are frames from the video, in chronological order.\n\n' +
      `App name: ${asset.name}\n` +
      `App description / tone: ${asset.description}\n` +
      `Link (include verbatim): ${asset.link}\n` +
      `Hashtags (include at the end): ${asset.hashtags}\n\n` +
      'Write the caption now.',
  })
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': getAnthropicKey(),
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
    },
    body: JSON.stringify({
      model: CLAUDE_MODEL,
      max_tokens: 1024,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content }],
    }),
  })
  if (!res.ok) {
    let msg = `Claude API HTTP ${res.status}`
    try {
      msg = (await res.json()).error?.message || msg
    } catch { /* keep status */ }
    throw new Error(msg)
  }
  const data = await res.json()
  const text = (data.content || [])
    .filter((b) => b.type === 'text')
    .map((b) => b.text)
    .join('')
    .trim()
  if (!text) throw new Error('empty caption from Claude')
  return text
}

// Generates captions for every done request in the batch and writes them
// back to the release. onProgress(done, total) reports per-video progress.
export async function generateCaptionsForBatch(backend, batch, assets, onProgress) {
  if (!getAnthropicKey()) throw new Error(STR.captions.needKey)
  const targets = batch.requests.filter((r) => r.status === 'done' && r.has_output)
  if (!targets.length) return 0
  const newCaptions = {}
  let done = 0
  for (const r of targets) {
    const asset = assets.find((a) => String(a.id) === String(r.asset_id)) || {
      name: '', description: '', link: '', hashtags: '',
    }
    const blob = await backend.fetchVideoBlob(r)
    const introOffset = r._hasIntro ? 2.5 : 0
    const clipLen = Math.max(1, (r.cut_seconds || 0) - (r.start_seconds || 0))
    const frames = await extractFrames(blob, clipLen, introOffset)
    newCaptions[r._videoName] = await callClaude(frames, asset)
    done += 1
    if (onProgress) onProgress(done, targets.length)
  }
  await backend.applyCaptions(batch, newCaptions)
  return done
}
