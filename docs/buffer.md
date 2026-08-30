# Buffer distribution — how it works and how to add a brand

## Why Buffer

Zernio's free tier covers 2 connected socials per account; the plan is
5 brands × 3 socials (Facebook, Instagram, TikTok) ≈ $78/month there.
Buffer Free gives each **separate Buffer account**: 3 channels, one API
key, 3,000 API calls / 30 days, scheduling + publishing via API. One
Buffer account per brand ⇒ $0/month.

Zernio is **not removed** — it stays the default driver and the
fallback. A brand publishes through Buffer only when it opts in via
`distributor: "buffer"` in `data/aimodels/roster.json`. Switching a
brand back = deleting that field.

## Architecture

- `scripts/distributors/buffer.py` — the driver (same pluggable
  contract as the Zernio driver). GraphQL, single endpoint
  `https://api.buffer.com`, `Authorization: Bearer <key>`.
- `scripts/distribute_aimodels.py` — picks the driver **per brand**,
  swaps the brand's key into `BUFFER_ACCESS_TOKEN`, and handles
  Buffer's quirks (below).
- The external post ids are stored in the item's existing
  `zernioPostId` field (the pipeline-wide idempotency key);
  `distribution.via: "buffer"` says who the id belongs to. Renaming the
  field everywhere is a post-migration cleanup.

### Buffer facts the code is built around

| Fact | Consequence in code |
|---|---|
| 1 API key per account | One Buffer account + one `BUFFER_TOKEN_<BRAND>` secret per brand |
| Media by **public URL** only | We pass GitHub-Pages / raw-hosted URLs (already public); no byte upload exists |
| Queue cap: **10 pending posts per channel** | A full queue → item held in `queue_wait`, retried next hourly run; never failed, never lost. Note: if the item's slot passes while waiting, re-slot it in the tab. |
| 3,000 calls / 30 days (Free) | No tight polling: a scheduled post is not status-checked until ~5 min before its `dueAt`; queue checks run only when something is about to be sent; 429s retry with backoff honoring `Retry-After` |
| One `createPost` per channel | Multi-platform = one post per channel; ids joined `id1,id2,id3` under one item |

Failures surface exactly like Zernio failures: `distribution.state:
"failed"` on the item (visible in the tab) + a notice in the
**Zernio Inbox** tab.

## Adding a brand (owner checklist)

1. **Buffer account**: create a fresh Buffer account for the brand
   (Free plan), connect its Facebook, Instagram and TikTok channels in
   the Buffer dashboard.
2. **API key**: Buffer → Settings → API → create the key
   (publish.buffer.com/settings/api).
3. **Secret**: repo → Settings → Secrets and variables → Actions → new
   secret `BUFFER_TOKEN_<BRAND>` (brand id uppercased, e.g.
   `BUFFER_TOKEN_RUBY`) with the key as value.
4. **Workflow env**: add one line in **both**
   `.github/workflows/distribute-aimodels.yml` and
   `.github/workflows/buffer-test.yml`:
   `BUFFER_TOKEN_<BRAND>: ${{ secrets.BUFFER_TOKEN_<BRAND> }}`.
5. **Roster**: on the brand's entry in `data/aimodels/roster.json` set
   `"distributor": "buffer"`. (Optional: `"bufferKeyEnv"` to override
   the secret name.) Leave `bufferChannels` out — it auto-wires.
6. **Doctor**: Actions → **buffer-test** → Run workflow → brand id +
   action `doctor`. It verifies the key, prints the channels and queue
   depths, and commits `bufferChannels: {facebook: …, instagram: …,
   tiktok: …}` to the roster automatically.
7. **Test post**: same workflow with action `test-post`
   (`in_minutes: 10` schedules it 10 minutes out so it can still be
   deleted from the Buffer dashboard; `0` publishes immediately). Check
   the post on each platform.
8. Approve → the brand's regular scheduled/publish-now flow now runs
   through Buffer. Nothing else changes.

## Where brands live

A "brand" is either an app venture in `assets.json` (Planty) or a
character in `data/aimodels/roster.json`. Both runners
(`distribute_apps.py` / `distribute_aimodels.py`) share one wiring
implementation (`scripts/buffer_wiring.py`), and the owner scripts look
a brand up by **id or name** across both files — `planty` finds the
venture whose legacy id is `sample`.

## Video hosting for app ventures

Buffer fetches media **at publish time** from a public direct URL.
Ledger videos live in GitHub Releases (octet-stream behind a redirect —
not Buffer-safe), so the Pages deploy mirrors every video a Buffer
venture still needs to `/media/apps-<tag>-<name>` (real `video/mp4`),
and the runner uses that URL. If the mirror copy isn't live yet the
runner kicks the Pages deploy, waits a few minutes, and otherwise holds
the entry in `media_wait` for the next hourly run — never failed, never
lost.

## Migration status

- **Phase 1 (current)**: **Planty** is wired as the first Buffer brand
  (`assets.json`: `distributor: "buffer"`, secret
  `BUFFER_TOKEN_PLANTY`). Her Zernio wiring stays in place — posts that
  were **already scheduled through Zernio keep syncing and publishing
  through Zernio** (an entry follows the service that holds it,
  `distribution.via`); only new sends go through Buffer. **Stop after
  Planty's test posts — owner approval required before migrating the
  remaining brands.**
- A brand set to `buffer` whose token is missing is **skipped with a
  notice** — it never silently falls back to Zernio, so a migrated
  brand can't surprise-publish through the old account.
- ruby (characters side) was un-wired from Buffer per the owner —
  characters stay on Zernio until further notice.
