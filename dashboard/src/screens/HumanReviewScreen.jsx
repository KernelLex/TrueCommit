import { useCallback, useState } from 'react'
import { api, usePolling } from '../api'

// Packet P9. This screen used to be read-only — it filtered the audit log for
// two action kinds and said so honestly. It now reads GET /api/review-queue and
// the buttons really act: every one of them is a POST that the ledger audits
// BEFORE it takes effect, and that re-runs check_bounds() on anything it is
// asked to send.
//
// Three things this screen must never quietly hide:
//  1. An approve can come back { blocked: true }. That is not an error — it is
//     the touch cap (or a terminal state) refusing a hold that went stale while
//     it sat here. It gets its own toast wording, not a success one.
//  2. The formal-notice draft has NO approve button, by design. The agent never
//     sends legal communication, human click or not (CLAUDE.md law 4). Its only
//     button is "Mark handled", which sends nothing.
//  3. Empty sections stay honest. "Nothing held" means the gates never fired,
//     not that the feature is missing.

function fmtTs(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return ts
  }
}

function fmtInr(n) {
  if (n === null || n === undefined) return '—'
  return `Rs.${Number(n).toLocaleString('en-IN')}`
}

function Banner({ error }) {
  if (!error) return null
  return <div className="banner banner-error">Could not load the review queue ({error.message}).</div>
}

function HeldCard({ row, busy, onApprove, onReject, onHandled }) {
  const legal = row.sendable === false
  const stale = row.entity_state && ['KEPT', 'CLEAN_LOSS', 'HUMAN_HANDOFF', 'DISPUTED'].includes(row.entity_state)

  return (
    <li className={`review-item ${legal ? 'review-item-legal' : ''}`}>
      <div className="review-item-head">
        <span className="review-entity">{row.entity_id}</span>
        <span className="review-kind">
          {legal ? 'formal notice — merchant sends' : `${row.action.kind} held`}
        </span>
      </div>

      <div className="review-summary">
        {legal
          ? 'Formal notice draft. The agent never sends legal communication — send it yourself, then mark it handled here.'
          : `${row.action.kind} for ${fmtInr(row.amount_inr)} was decided but not sent.`}
      </div>
      <div className="review-reason">Held because: {row.reason}</div>

      <div className="review-meta">
        <span>state <strong>{row.entity_state || '—'}</strong></span>
        {row.debtor_id && <span>debtor <strong>{row.debtor_id}</strong></span>}
        {row.extraction_confidence !== null && row.extraction_confidence !== undefined && (
          <span>extraction confidence <strong>{row.extraction_confidence}</strong></span>
        )}
        <span>bounds re-checked on approval</span>
      </div>

      {stale && !legal && (
        <div className="review-warn">
          This entity is already <strong>{row.entity_state}</strong>. Approving will re-run{' '}
          <code>check_bounds()</code> against its state now, and will almost certainly be refused —
          which is the gate working, not a bug.
        </div>
      )}

      <div className="review-actions">
        {legal ? (
          <button type="button" className="btn btn-neutral" disabled={busy} onClick={() => onHandled(row)}>
            Mark handled
          </button>
        ) : (
          <>
            <button type="button" className="btn btn-approve" disabled={busy} onClick={() => onApprove(row)}>
              Approve
            </button>
            <button type="button" className="btn btn-reject" disabled={busy} onClick={() => onReject(row)}>
              Reject
            </button>
          </>
        )}
        <span className="review-ts">held {fmtTs(row.ts)}</span>
      </div>
    </li>
  )
}

function HandoffCard({ row, busy, onResolve, onPause }) {
  const card = row.evidence && row.evidence.card

  return (
    <li className="review-item review-item-handoff">
      <div className="review-item-head">
        <span className="review-entity">{row.entity_id}</span>
        <span className="review-kind">{row.state}</span>
      </div>

      <div className="review-summary">
        {fmtInr(row.amount_inr)}
        {row.debtor_id ? ` · ${row.debtor_id}` : ''}
        {row.escalate_stage ? ` · reached escalation stage ${row.escalate_stage}` : ''}
      </div>
      {row.reason_detail && <div className="review-reason">{row.reason_detail}</div>}

      {card && (
        <details className="review-evidence">
          <summary>Evidence packet</summary>
          <pre>{card}</pre>
        </details>
      )}

      <div className="review-actions">
        <button type="button" className="btn btn-approve" disabled={busy} onClick={() => onResolve(row, 'recovered')}>
          Resolve: recovered
        </button>
        <button type="button" className="btn btn-neutral" disabled={busy} onClick={() => onResolve(row, 'written_off')}>
          Resolve: written off
        </button>
        {!row.paused && (
          <button type="button" className="btn btn-reject" disabled={busy} onClick={() => onPause(row)}>
            Pause thread
          </button>
        )}
      </div>
    </li>
  )
}

export default function HumanReviewScreen() {
  const { data, error, loading, refetch } = usePolling(() => api.reviewQueue(), { intervalMs: 4000 })
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  // Optimistic in the sense that matters here: act, then immediately re-read
  // the queue rather than waiting up to 4s for the next poll. We never patch
  // the local list by hand — the server's answer is the only truth about what
  // happened, because it is the side that ran check_bounds().
  const act = useCallback(
    async (fn, describe) => {
      setBusy(true)
      try {
        const result = await fn()
        setNotice(describe(result))
      } catch (e) {
        setNotice({ kind: 'error', text: e.message })
      } finally {
        setBusy(false)
        refetch()
      }
    },
    [refetch],
  )

  const onApprove = (row) =>
    act(
      () => api.approveHeld(row.id),
      (r) =>
        r.blocked
          ? { kind: 'error', text: `${row.entity_id}: approved, then refused at click time — ${r.block_reason}` }
          : { kind: 'success', text: `${row.entity_id}: ${r.emitted.kind} sent (${r.emitted.id}).` },
    )

  const onReject = (row) =>
    act(
      () => api.rejectHeld(row.id),
      (r) => ({
        kind: 'info',
        text: r.fallback
          ? `${row.entity_id}: rejected — fell back to ${r.fallback.kind}.`
          : `${row.entity_id}: rejected. The ladder resumes at its next beat.`,
      }),
    )

  const onHandled = (row) =>
    act(
      () => api.markHeldHandled(row.id, 'marked handled by the merchant from the review queue'),
      () => ({ kind: 'info', text: `${row.entity_id}: formal notice marked handled. Nothing was sent by the agent.` }),
    )

  const onResolve = (row, resolution) =>
    act(
      () => api.resolveHandoff(row.entity_id, resolution),
      (r) => ({ kind: 'success', text: `${row.entity_id} closed as ${r.entity.state}.` }),
    )

  const onPause = (row) =>
    act(() => api.pauseEntity(row.entity_id), () => ({ kind: 'info', text: `${row.entity_id} paused.` }))

  const onUnpause = (row) =>
    act(() => api.unpauseEntity(row.entity_id), () => ({ kind: 'info', text: `${row.entity_id} unpaused.` }))

  const held = (data && data.held_actions) || []
  const handoffs = (data && data.handoffs) || []
  const disputes = (data && data.disputes) || []
  const paused = (data && data.paused) || []
  const gates = (data && data.gates) || {}
  const open = [...handoffs, ...disputes]

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>Human-Review Queue</h1>
        {data && (
          <span className="tier-badge">
            day {data.day} · {held.length} held · {open.length} open · {paused.length} paused
          </span>
        )}
      </div>
      <p className="screen-sub">
        The merchant's approval queue (master doc §3.6) — held money actions, the formal-notice draft, and every
        case the ladder stopped on. Gates in force:{' '}
        <code>money action needs confidence ≥ {gates.money_action_confidence ?? '—'}</code>,{' '}
        <code>clarify below {gates.clarify_confidence ?? '—'}</code>. Every button here is a POST the ledger audits
        before it acts; approval re-runs <code>check_bounds()</code> at click time, so a stale hold cannot slip past a
        cap that has since been hit.
      </p>

      <Banner error={error} />
      {notice && <div className={`banner banner-${notice.kind === 'error' ? 'error' : 'info'}`}>{notice.text}</div>}

      <section className="panel">
        <h2>Held actions</h2>
        <p className="panel-sub">
          Decided by the ledger, deliberately not executed. Low-confidence money actions wait for an Approve; the
          formal-notice draft has no Approve at all — the agent never sends legal communication.
        </p>
        {held.length === 0 ? (
          <div className="empty-state">
            <p>
              {loading
                ? 'Loading…'
                : 'Nothing held. That means every money action so far came from an extraction the perception layer was confident enough about (≥ 0.90), and the ladder has not reached a formal-notice stage. Advance the clock and items will appear here on their own — this list is not hardcoded.'}
            </p>
          </div>
        ) : (
          <ul className="review-list">
            {held.map((row) => (
              <HeldCard
                key={row.id}
                row={row}
                busy={busy}
                onApprove={onApprove}
                onReject={onReject}
                onHandled={onHandled}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <h2>Open handoffs &amp; disputes</h2>
        <p className="panel-sub">
          Where the ladder stopped: HUMAN_HANDOFF (escalation exhausted or the hard step cap) and DISPUTED (instant
          stop, evidence packet, no further outbound). Resolving one is the only event in the system allowed to move a
          terminal state — it exists because a human is acting.
        </p>
        {open.length === 0 ? (
          <div className="empty-state">
            <p>
              {loading
                ? 'Loading…'
                : 'Nothing waiting on a human yet. Expected at day 0 — no events have been processed, so nothing has reached HUMAN_HANDOFF or DISPUTED.'}
            </p>
          </div>
        ) : (
          <ul className="review-list">
            {open.map((row) => (
              <HandoffCard key={row.entity_id} row={row} busy={busy} onResolve={onResolve} onPause={onPause} />
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <h2>Paused threads</h2>
        <p className="panel-sub">
          The merchant kill-switch. A paused thread is skipped by the outreach loop and refused every outbound action
          inside the ledger's own gate — pausing stops it, it does not merely hide it.
        </p>
        {paused.length === 0 ? (
          <div className="empty-state">
            <p>No paused threads. Pause any open handoff above with one click.</p>
          </div>
        ) : (
          <ul className="review-list">
            {paused.map((row) => (
              <li className="review-item review-item-paused" key={row.entity_id}>
                <div className="review-item-head">
                  <span className="review-entity">{row.entity_id}</span>
                  <span className="review-kind">paused</span>
                </div>
                <div className="review-summary">
                  {fmtInr(row.amount_inr)} · state {row.state || '—'}
                </div>
                <div className="review-actions">
                  <button type="button" className="btn btn-approve" disabled={busy} onClick={() => onUnpause(row)}>
                    Unpause
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
