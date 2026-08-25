import { api, usePolling } from '../api'
import BetaCurve from '../components/BetaCurve'

function fmtTs(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return ts
  }
}

export default function TrustCurvesScreen() {
  const { data: trustList, error, loading } = usePolling(() => api.trustList(), { intervalMs: 3000 })
  const rows = Array.isArray(trustList) ? [...trustList].sort((a, b) => a.debtor_id.localeCompare(b.debtor_id)) : []

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>Trust Curves</h1>
      </div>
      <p className="screen-sub">
        Per-debtor Beta(α, β) posterior over "will this debtor keep a promise," drawn as a density curve — no chart
        library, computed client-side from <code>GET /api/trust</code>: 100 points of
        f(x) ∝ x^(α−1)(1−x)^(β−1), normalized by the max. Prior is Beta(2, 2); +1 α on a kept promise, +1 β on a
        broken one, with decay toward the prior over time.
      </p>

      {error && <div className="banner banner-error">Could not load trust records ({error.message}).</div>}

      {!loading && rows.length === 0 && !error && (
        <div className="empty-state">
          <p>
            No trust records yet. A debtor gets a Beta(2, 2) prior the first time any event touches one of their
            invoices, and it moves on <code>promise_kept</code> / <code>promise_broken</code> /{' '}
            <code>mandate_execute_success</code> / <code>mandate_execute_failed</code> / <code>mandate_refused</code>.
            With the current dataset freshly loaded and no events processed, this queue is expected to be empty.
          </p>
        </div>
      )}

      <div className="trust-grid">
        {rows.map((t) => {
          const mean = t.alpha / (t.alpha + t.beta)
          return (
            <div className="trust-card" key={t.debtor_id}>
              <div className="trust-card-head">
                <span className="trust-debtor">{t.debtor_id}</span>
                <span className="trust-mean">mean {mean.toFixed(2)}</span>
              </div>
              <BetaCurve alpha={t.alpha} beta={t.beta} />
              <div className="trust-stats">
                <span>α = {t.alpha.toFixed(2)}</span>
                <span>β = {t.beta.toFixed(2)}</span>
                <span className="trust-ts">updated {fmtTs(t.last_update)}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
