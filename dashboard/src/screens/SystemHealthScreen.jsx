import { api, usePolling } from '../api'
import { NUMERIC_BOUNDS, POLICY_BOUNDS } from '../bounds'

export default function SystemHealthScreen() {
  const { data: health, error, loading } = usePolling(() => api.health(), { intervalMs: 3000 })
  const online = !error && health && health.status === 'ok'

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>System Health</h1>
      </div>

      <div className="panel">
        <h2>API status</h2>
        <div className="health-row">
          <span className={`status-dot ${online ? 'status-dot-ok' : loading ? 'status-dot-pending' : 'status-dot-down'}`} />
          <span className="health-status-label">{loading && !health ? 'Checking…' : online ? 'Online' : 'Unreachable'}</span>
          {error && <span className="health-error">{error.message}</span>}
        </div>
        {health && (
          <div className="health-stats">
            <div>
              <span className="health-stat-value">{health.invoices_loaded}</span>
              <span className="health-stat-label">invoices loaded</span>
            </div>
            <div>
              <span className="health-stat-value">{health.reserves_active}</span>
              <span className="health-stat-label">reserves active</span>
            </div>
          </div>
        )}
      </div>

      <div className="panel">
        <h2>Bounds reference</h2>
        <p className="panel-sub">
          Hardcoded here from <code>engine/judgment/state_machine.py</code> — these are named constants, not
          something the API serves, so this card mirrors the source rather than fetching it (CLAUDE.md law #4: "all
          bounds live as constants").
        </p>
        <table className="bounds-table">
          <thead>
            <tr>
              <th>Constant</th>
              <th>Value</th>
              <th>What it caps</th>
            </tr>
          </thead>
          <tbody>
            {NUMERIC_BOUNDS.map((b) => (
              <tr key={b.name}>
                <td>
                  <code>{b.name}</code>
                </td>
                <td className="bounds-value">{b.value}</td>
                <td>{b.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="panel-sub" style={{ marginTop: '1rem' }}>
          Plus three policy bounds that aren&apos;t single numbers but are enforced by the same <code>check_bounds()</code> gate:
        </p>
        <ul className="policy-list">
          {POLICY_BOUNDS.map((b) => (
            <li key={b.name}>
              <strong>{b.name}</strong> — {b.detail}
            </li>
          ))}
        </ul>
      </div>

      <div className="panel">
        <h2>Not wired live yet</h2>
        <div className="placeholder-row">
          <span className="placeholder-label">Dead-letter count</span>
          <span className="placeholder-value">— not wired yet</span>
        </div>
        <div className="placeholder-row">
          <span className="placeholder-label">Perception provider</span>
          <span className="placeholder-value">Anthropic claude-sonnet-5 (planned) — Phase B, not live yet</span>
        </div>
      </div>
    </div>
  )
}
