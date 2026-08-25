import { usePolling } from '../api'
import { api } from '../api'

// State -> funnel bucket. Mirrors P4's spec exactly:
//   at risk        = NEW / TRIAGED / ENGAGED
//   in recovery     = PROMISED / MANDATED / LINKED / AT_RISK / ESCALATE_1..4
//   recovered       = KEPT
//   own small bucket = DISPUTED / HUMAN_HANDOFF / CLEAN_LOSS
const BUCKET_OF_STATE = {
  NEW: 'at_risk',
  TRIAGED: 'at_risk',
  ENGAGED: 'at_risk',
  PROMISED: 'in_recovery',
  MANDATED: 'in_recovery',
  LINKED: 'in_recovery',
  AT_RISK: 'in_recovery',
  ESCALATE_1: 'in_recovery',
  ESCALATE_2: 'in_recovery',
  ESCALATE_3: 'in_recovery',
  ESCALATE_4: 'in_recovery',
  KEPT: 'recovered',
  DISPUTED: 'other',
  HUMAN_HANDOFF: 'other',
  CLEAN_LOSS: 'other',
}

const BUCKETS = [
  { key: 'at_risk', label: 'At Risk', hint: 'NEW · TRIAGED · ENGAGED', color: 'var(--warn)' },
  { key: 'in_recovery', label: 'In Recovery', hint: 'PROMISED → ESCALATE_4', color: 'var(--accent)' },
  { key: 'recovered', label: 'Recovered', hint: 'KEPT', color: 'var(--good)' },
  { key: 'other', label: 'Disputed / Handoff / Loss', hint: 'DISPUTED · HUMAN_HANDOFF · CLEAN_LOSS', color: 'var(--muted-strong)' },
]

function inr(n) {
  return `Rs.${n.toLocaleString('en-IN')}`
}

function aggregate(entities) {
  const totals = { at_risk: { sum: 0, count: 0 }, in_recovery: { sum: 0, count: 0 }, recovered: { sum: 0, count: 0 }, other: { sum: 0, count: 0 } }
  const byState = {}
  for (const e of entities) {
    const bucket = BUCKET_OF_STATE[e.state] || 'other'
    const amount = e.invoice_amount_inr || 0
    totals[bucket].sum += amount
    totals[bucket].count += 1
    byState[e.state] = (byState[e.state] || 0) + 1
  }
  return { totals, byState }
}

export default function FunnelScreen() {
  const { data: entities, error, loading } = usePolling(() => api.entities(), { intervalMs: 3000 })

  const rows = Array.isArray(entities) ? entities : []
  const { totals, byState } = aggregate(rows)
  const grandTotal = rows.reduce((s, e) => s + (e.invoice_amount_inr || 0), 0)

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>Funnel</h1>
        <span className="tier-badge tier-badge-2">Tier 2 — simulated recovery</span>
      </div>
      <p className="screen-sub">
        Every figure below is aggregated live from <code>GET /api/entities</code> — nothing is invented in the
        frontend. This funnel groups the state machine&apos;s states into money buckets so the shape of a recovery
        batch reads in one glance.
      </p>

      {error && <div className="banner banner-error">Could not reach the API ({error.message}). Showing last known data.</div>}
      {loading && !rows.length && <div className="banner banner-info">Loading entities…</div>}

      <div className="funnel-grid">
        {BUCKETS.map((b) => (
          <div className="funnel-card" key={b.key} style={{ '--bucket-color': b.color }}>
            <div className="funnel-card-label">{b.label}</div>
            <div className="funnel-card-amount">{inr(totals[b.key].sum)}</div>
            <div className="funnel-card-count">
              {totals[b.key].count} invoice{totals[b.key].count === 1 ? '' : 's'}
            </div>
            <div className="funnel-card-hint">{b.hint}</div>
          </div>
        ))}
      </div>

      <div className="funnel-total">Total tracked: {inr(grandTotal)} across {rows.length} invoices</div>

      <div className="panel">
        <h2>By state</h2>
        {rows.length === 0 ? (
          <p className="empty-note">No entities loaded yet.</p>
        ) : (
          <div className="state-chip-row">
            {Object.entries(byState)
              .sort((a, b) => b[1] - a[1])
              .map(([state, count]) => (
                <span className="state-chip" key={state}>
                  <span className="state-chip-name">{state}</span>
                  <span className="state-chip-count">{count}</span>
                </span>
              ))}
          </div>
        )}
      </div>
    </div>
  )
}
