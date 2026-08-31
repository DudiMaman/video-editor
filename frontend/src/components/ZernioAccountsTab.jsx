import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const Z = STR.zernioTab

// Per-distributor secret names (mirror key_env_of in
// scripts/buffer_wiring.py / scripts/distribute_apps.py). Every
// BUFFER_TOKEN_* / ZERNIO_KEY_* repo secret is picked up by the
// workflows automatically - pasting the secret is the only setup step.
const idSlug = (a) => String(a.id).replace(/[^A-Za-z0-9]/g, '_').toUpperCase()
const secretNameOf = (a) =>
  a.distributor === 'buffer'
    ? (a.bufferKeyEnv || `BUFFER_TOKEN_${idSlug(a)}`)
    : (a.zernioKeyEnv || `ZERNIO_KEY_${idSlug(a)}`)

const loginLabel = (v) =>
  v === 'google' ? STR.assets.zernioLoginGoogle
    : v === 'email' ? STR.assets.zernioLoginEmail
    : '—'

const PLATFORM_LABEL = {
  instagram: 'Instagram', tiktok: 'TikTok', facebook: 'Facebook',
  youtube: 'YouTube', twitter: 'X', threads: 'Threads', linkedin: 'LinkedIn',
  pinterest: 'Pinterest',
}

// The connected platforms for a venture through its current distributor.
const platformsOf = (a) => {
  if (a.distributor === 'buffer') {
    return Object.keys(a.bufferChannels || {})
      .filter((p) => a.bufferChannels[p])
      .map((p) => PLATFORM_LABEL[p] || p)
  }
  const tg = Array.isArray(a.zernioTargets) ? a.zernioTargets : []
  if (tg.length) return tg.map((t) => PLATFORM_LABEL[t.platform] || t.platform)
  return a.zernioAccountId ? ['Instagram'] : []
}

// The owner's record fields for whichever account the venture uses.
const recordOf = (a) =>
  a.distributor === 'buffer'
    ? { name: a.bufferAccountName, email: a.bufferEmail, login: a.bufferLogin }
    : { name: a.zernioAccountName, email: a.zernioEmail, login: a.zernioLogin }

// Read-only registry of every venture (asset) and its distribution
// account - Buffer for migrated ventures, legacy Zernio otherwise.
// Editing stays in the assets tab; adding a project is: create the
// asset there, pick Buffer, open a Buffer account, paste its key as the
// secret named in this table - channels wire themselves.
export default function ZernioAccountsTab({ active }) {
  const [assets, setAssets] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!active) return
    backend.listAssets().then(setAssets).catch((e) => setError(e.message))
  }, [active])

  if (error) return <p className="error">{STR.errors.generic}{error}</p>
  if (assets === null) return <p className="empty">…</p>

  return (
    <div>
      <h2>{Z.title}</h2>
      <p className="hint">{Z.explain}</p>
      {assets.length === 0 ? (
        <p className="empty">{Z.empty}</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead>
              <tr>
                {[Z.colProject, Z.colDistributor, Z.colAccountName, Z.colEmail,
                  Z.colLogin, Z.colStatus, Z.colSecret].map((h) => (
                  <th key={h} style={{ textAlign: 'start', padding: '8px 10px', borderBottom: '2px solid var(--border,#ccc)', whiteSpace: 'nowrap' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => {
                const rec = recordOf(a)
                const isBuffer = a.distributor === 'buffer'
                return (
                  <tr key={a.id}>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)', fontWeight: 600 }}>
                      {a.name}
                    </td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                      <span style={{ fontSize: 12, fontWeight: 700, borderRadius: 6, padding: '2px 8px', background: isBuffer ? '#e6f0ff' : '#f0f0f2', color: isBuffer ? '#1d4ed8' : '#555' }}>
                        {isBuffer ? 'Buffer' : 'Zernio'}
                      </span>
                    </td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                      {rec.name || '—'}
                    </td>
                    <td dir="ltr" style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                      {rec.email || '—'}
                    </td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                      {loginLabel(rec.login)}
                    </td>
                    <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                      {platformsOf(a).length
                        ? <span style={{ color: '#22a06b', fontWeight: 600 }}>{platformsOf(a).join(', ')}</span>
                        : <span style={{ opacity: 0.6 }}>{Z.notConnected}</span>}
                    </td>
                    <td dir="ltr" style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)', fontFamily: 'monospace', fontSize: 12 }}>
                      {secretNameOf(a)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint" style={{ marginTop: 10 }}>{Z.editHint}</p>
    </div>
  )
}
