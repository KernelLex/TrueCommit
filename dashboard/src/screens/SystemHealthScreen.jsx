import { api, usePolling } from '../api'
import { NUMERIC_BOUNDS, POLICY_BOUNDS } from '../bounds'

const SOURCE_LABEL = { env: 'env var', yaml: 'config/agents.yaml', builtin: 'builtin default' }

function SourceTag({ source }) {
  if (!source) return null
  return <span className="placeholder-value" style={{ marginLeft: '0.5rem', fontStyle: 'normal' }}>
    ({SOURCE_LABEL[source] || source})
  </span>
}

export default function SystemHealthScreen() {
  const { data: health, error, loading } = usePolling(() => api.health(), { intervalMs: 3000 })
  const { data: config, error: configError } = usePolling(() => api.config(), { intervalMs: 5000 })
  const online = !error && health && health.status === 'ok'

  const live = config?.live_status
  const eff = config?.effective?.perception
  const effSentinel = config?.effective?.sentinel
  const rawSentinel = config?.config?.sentinel
  const auditor = config?.config?.auditor
  const cache = live?.cache_stats

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
        <h2>Reliability mesh — live status</h2>
        <p className="panel-sub">
          Every number below comes from <code>GET /config</code> (<code>api/main.py</code>), reading the process
          that is actually running this demo — not a static guess.
        </p>
        {configError && <div className="health-error">{configError.message}</div>}
        {live && (
          <>
            <div className="health-stats">
              <div>
                <span className="health-stat-value">{live.runtime_provider}</span>
                <span className="health-stat-label">
                  perception provider{live.runtime_model ? ` — ${live.runtime_model}` : ''}
                </span>
              </div>
              <div>
                <span className="health-stat-value">{live.ollama_fallback_events}</span>
                <span className="health-stat-label">ollama→heuristic degradation events</span>
              </div>
              <div>
                <span className="health-stat-value">{live.sentinel_dead_letter_count}</span>
                <span className="health-stat-label">sentinel dead-letter count</span>
              </div>
            </div>
            {cache && (
              <div className="health-stats" style={{ marginTop: '0.75rem' }}>
                <div>
                  <span className="health-stat-value">{cache.hits}</span>
                  <span className="health-stat-label">perception cache hits</span>
                </div>
                <div>
                  <span className="health-stat-value">{cache.misses}</span>
                  <span className="health-stat-label">perception cache misses</span>
                </div>
                <div>
                  <span className="health-stat-value">{cache.writes}</span>
                  <span className="health-stat-label">perception cache writes</span>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="panel">
        <h2>Agent parameters</h2>
        <p className="panel-sub">
          Read-only mirror of <code>config/agents.yaml</code>, loaded by <code>engine/config.py</code>. Precedence:
          explicit argument (programmatic only) &gt; environment variable, if explicitly set &gt; this file, if the
          key is present &gt; the builtin default. The tag after each value says which of those actually won.
          Editing this file changes behaviour on the next process restart — nothing here is writable from the
          dashboard.
        </p>

        {eff && (
          <>
            <h3 style={{ fontSize: '0.85rem', margin: '0.9rem 0 0.4rem', color: 'var(--text-dim)' }}>
              Perception
            </h3>
            <ul className="policy-list">
              <li><strong>provider</strong> — <code>{eff.provider.value}</code><SourceTag source={eff.provider.source} /></li>
              <li><strong>ollama_model</strong> — <code>{eff.ollama_model.value}</code><SourceTag source={eff.ollama_model.source} /> <span className="placeholder-value">(only read when provider is ollama)</span></li>
              <li><strong>ollama_base_url</strong> — <code>{eff.ollama_base_url.value}</code><SourceTag source={eff.ollama_base_url.source} /></li>
              <li><strong>cache_enabled</strong> — <code>{String(eff.cache_enabled.value)}</code><SourceTag source={eff.cache_enabled.source} /></li>
            </ul>
            <p className="panel-sub" style={{ margin: '0.5rem 0 0' }}>
              <code>cache_enabled</code> and the Sentinel values below are wired live. <code>provider</code> /
              <code>ollama_model</code> / <code>ollama_base_url</code> are resolved and reported here, but this
              packet does not rewire the running perception provider — set the matching environment variable to
              actually switch it today (env already wins over this file, so nothing regresses).
            </p>

            <h3 style={{ fontSize: '0.85rem', margin: '1.1rem 0 0.4rem', color: 'var(--text-dim)' }}>
              Sentinel <span className="placeholder-value">(wired)</span>
            </h3>
            <ul className="policy-list">
              <li><strong>max_retries</strong> — <code>{effSentinel.max_retries}</code></li>
              <li><strong>backoff_minutes</strong> — <code>[{effSentinel.backoff_minutes.join(', ')}]</code></li>
              <li><strong>link_open_timeout_hours</strong> — <code>{effSentinel.link_open_timeout_hours}</code></li>
              <li><strong>circuit_breaker_threshold</strong> — <code>{effSentinel.circuit_breaker_threshold}</code></li>
            </ul>
            {rawSentinel && (
              <p className="panel-sub" style={{ margin: '0.4rem 0 0' }}>
                Change any of these in <code>config/agents.yaml</code> and Sentinel's actual retry/backoff/circuit
                behaviour changes with it — see <code>tests/test_config.py</code>.
              </p>
            )}

            <h3 style={{ fontSize: '0.85rem', margin: '1.1rem 0 0.4rem', color: 'var(--text-dim)' }}>
              Auditor <span className="placeholder-value">(not wired until the Auditor packet, Day 7)</span>
            </h3>
            {auditor && (
              <ul className="policy-list">
                <li><strong>sample_rate</strong> — <code>{auditor.sample_rate}</code></li>
                <li><strong>quarantine_threshold</strong> — <code>{auditor.quarantine_threshold}</code></li>
              </ul>
            )}

            <h3 style={{ fontSize: '0.85rem', margin: '1.1rem 0 0.4rem', color: 'var(--text-dim)' }}>
              Judgment
            </h3>
            <p className="panel-sub" style={{ margin: 0 }}>
              No tunables — by design. CLAUDE.md law 4: all bounds live as constants in
              <code> engine/judgment/state_machine.py</code> and are never configurable, from this file or any
              other. See the bounds card below.
            </p>
          </>
        )}
        {!eff && !configError && <p className="panel-sub">Loading…</p>}
      </div>

      <div className="panel">
        <h2>Bounds reference — hard-coded by design, not configurable</h2>
        <p className="panel-sub">
          Named constants at the top of <code>engine/judgment/state_machine.py</code> (CLAUDE.md law #4: "all
          bounds live as constants"). <code>GET /config</code> echoes these same values for display, but there is
          no write path anywhere — this card mirrors the source rather than offering a form, on purpose.
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
    </div>
  )
}
