import { useEffect, useState } from 'react'
import { STR } from '../strings.js'
import { backend } from '../backend/index.js'

const Z = STR.zernioTab

// The per-venture Zernio secret name (mirrors key_env_of() in
// scripts/distribute_apps.py and secretNameOf in AssetsTab).
const secretNameOf = (a) =>
  a.zernioKeyEnv ||
  'ZERNIO_KEY_' + String(a.id).replace(/[^A-Za-z0-9]/g, '_').toUpperCase()

const loginLabel = (v) =>
  v === 'google' ? STR.assets.zernioLoginGoogle
    : v === 'email' ? STR.assets.zernioLoginEmail
    : '—'

const PLATFORM_LABEL = {
  instagram: 'Instagram', tiktok: 'TikTok', facebook: 'Facebook',
  youtube: 'YouTube', twitter: 'X', threads: 'Threads', linkedin: 'LinkedIn',
  pinterest: 'Pinterest',
}

// The connected platforms for a venture: the multi-platform targets when
// present, else the legacy single Instagram account.
const platformsOf = (a) => {
  const tg = Array.isArray(a.zernioTargets) ? a.zernioTargets : []
  if (tg.length) return tg.map((t) => PLATFORM_LABEL[t.platform] || t.platform)
  return a.zernioAccountId ? ['Instagram'] : []
}

// Read-only overview of every venture (asset) and its Zernio account
// details, as recorded in the assets tab. Editing stays there - this
// tab is the registry view the owner asked for.
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
                {[Z.colProject, Z.colAccountName, Z.colEmail, Z.colLogin,
                  Z.colStatus, Z.colSecret].map((h) => (
                  <th key={h} style={{ textAlign: 'start', padding: '8px 10px', borderBottom: '2px solid var(--border,#ccc)', whiteSpace: 'nowrap' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {assets.map((a) => (
                <tr key={a.id}>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)', fontWeight: 600 }}>
                    {a.name}
                  </td>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                    {a.zernioAccountName || '—'}
                  </td>
                  <td dir="ltr" style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                    {a.zernioEmail || '—'}
                  </td>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border,#e2e2e6)' }}>
                    {loginLabel(a.zernioLogin)}
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
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint" style={{ marginTop: 10 }}>{Z.editHint}</p>
    </div>
  )
}
