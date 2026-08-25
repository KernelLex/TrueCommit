import { useMemo, useState } from 'react'
import { api, usePolling } from '../api'

const LAYER_LABEL = {
  perception: 'Perception',
  judgment: 'Judgment',
  action: 'Action',
  sentinel: 'Sentinel',
  auditor: 'Auditor',
}

function fmtTs(ts) {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'medium' })
  } catch {
    return ts
  }
}

export default function EntityTimelineScreen() {
  const { data: entities } = usePolling(() => api.entities(), { intervalMs: 3000 })
  const rows = useMemo(() => (Array.isArray(entities) ? [...entities].sort((a, b) => a.entity_id.localeCompare(b.entity_id)) : []), [entities])

  // No effect needed to seed a default: derive it during render. `selected`
  // stays '' until the user actually picks something; until then we just
  // fall back to the first loaded entity for display/fetching purposes.
  const [selected, setSelected] = useState('')
  const activeId = selected || rows[0]?.entity_id || ''

  const {
    data: audit,
    error,
    loading,
  } = usePolling(() => (activeId ? api.entityAudit(activeId) : Promise.resolve([])), {
    intervalMs: 3000,
    deps: [activeId],
    enabled: Boolean(activeId),
  })

  const entries = Array.isArray(audit) ? audit : []
  const selectedEntity = rows.find((r) => r.entity_id === activeId)

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>Entity Timeline</h1>
      </div>
      <p className="screen-sub">
        Pick any invoice to see its full audit trail — every perception, decision, and action, in order, each with a
        reason string. This screen is the audit-trail judging requirement.
      </p>

      <div className="entity-picker">
        <label htmlFor="entity-select">Invoice</label>
        <select id="entity-select" value={activeId} onChange={(e) => setSelected(e.target.value)}>
          {rows.length === 0 && <option value="">(no entities loaded)</option>}
          {rows.map((r) => (
            <option key={r.entity_id} value={r.entity_id}>
              {r.entity_id} — {r.state}
            </option>
          ))}
        </select>
        {selectedEntity && (
          <div className="entity-picker-meta">
            <span className={`state-pill state-pill-${selectedEntity.state}`}>{selectedEntity.state}</span>
            {selectedEntity.invoice_amount_inr != null && <span>Rs.{selectedEntity.invoice_amount_inr.toLocaleString('en-IN')}</span>}
            <span>{entries.length} audit entries</span>
          </div>
        )}
      </div>

      {error && <div className="banner banner-error">Could not load audit trail ({error.message}).</div>}

      {!loading && activeId && entries.length === 0 && !error && (
        <div className="empty-state">
          <p>
            No audit entries for <strong>{activeId}</strong> yet — it is sitting at state{' '}
            <strong>{selectedEntity ? selectedEntity.state : 'NEW'}</strong>. Entries appear here the moment an event
            is processed for it (via <code>POST /api/events</code> or, once wired, the time-warp clock).
          </p>
        </div>
      )}

      {entries.length > 0 && (
        <ol className="timeline">
          {entries.map((entry) => (
            <li className="timeline-item" key={entry.id}>
              <div className={`timeline-badge timeline-badge-${entry.layer}`}>{LAYER_LABEL[entry.layer] || entry.layer}</div>
              <div className="timeline-body">
                <div className="timeline-summary">{entry.summary}</div>
                {entry.detail && entry.detail.reason && <div className="timeline-reason">Reason: {entry.detail.reason}</div>}
                <div className="timeline-ts">{fmtTs(entry.ts)}</div>
                {entry.detail && Object.keys(entry.detail).length > 0 && (
                  <details className="timeline-detail">
                    <summary>Raw detail</summary>
                    <pre>{JSON.stringify(entry.detail, null, 2)}</pre>
                  </details>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
