import { api, usePolling } from '../api'

// The API has no dedicated "pending review" endpoint yet (that arrives in a
// later packet). Until it does, this screen reads the general audit log and
// surfaces the two action kinds that mean "a human needs to look at this":
// human_handoff (escalation ladder exhausted / hard step cap) and
// evidence_packet (dispute raised). Anything it shows is real audit data;
// when there is none yet, it says so instead of pretending.
const REVIEW_KINDS = ['human_handoff:', 'evidence_packet:']

function fmtTs(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'medium' })
  } catch {
    return ts
  }
}

export default function HumanReviewScreen() {
  const { data: audit, error, loading } = usePolling(() => api.audit(500), { intervalMs: 3000 })
  const rows = Array.isArray(audit) ? audit : []
  const items = rows.filter((a) => a.layer === 'action' && REVIEW_KINDS.some((k) => a.summary.startsWith(k)))

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>Human-Review Queue</h1>
      </div>
      <p className="screen-sub">
        Items land here when the escalation ladder reaches HUMAN_HANDOFF or a dispute produces an evidence packet —
        read live from <code>GET /api/audit</code>, filtered to those two action kinds. No held-action
        approve/reject workflow is wired yet; that is a later packet.
      </p>

      {error && <div className="banner banner-error">Could not load the audit log ({error.message}).</div>}

      {!loading && items.length === 0 && !error && (
        <div className="empty-state">
          <p>
            Queue is empty. That is expected right now — the dataset is freshly loaded with no events processed, so
            nothing has reached HUMAN_HANDOFF or DISPUTED yet. Once the escalation ladder or a dispute event fires
            (via <code>POST /api/events</code> or the time-warp clock), matching items will appear here automatically
            — this list is not hardcoded.
          </p>
        </div>
      )}

      {items.length > 0 && (
        <ul className="review-list">
          {items.map((a) => (
            <li className="review-item" key={a.id}>
              <div className="review-item-head">
                <span className="review-entity">{a.entity_id}</span>
                <span className="review-kind">{a.summary.split(':')[0]}</span>
              </div>
              <div className="review-summary">{a.summary}</div>
              {a.detail && a.detail.reason && <div className="review-reason">Reason: {a.detail.reason}</div>}
              <div className="review-foot">
                <span className="review-ts">{fmtTs(a.ts)}</span>
                <span className="review-pending">Awaiting merchant/human action — approve/dismiss workflow lands in a later packet</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
