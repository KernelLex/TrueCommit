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

// Shared across the Contacts panel, the Reminders panel and the Demo Console
// (packet P15) — one small badge distinguishing a REAL operator-submitted
// contact from the synthetic demo fallback, wherever a panel shows who a
// dispatch was addressed to.
function ContactSourceBadge({ source }) {
  const real = source === 'operator_submitted'
  return (
    <span className={`contact-source-badge ${real ? 'contact-source-badge-real' : 'contact-source-badge-demo'}`}>
      {real ? 'real contact on file' : 'demo fallback'}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Demo Console (packet P13) — "Create Mandate Now".
//
// This panel is deliberately styled and worded to look like NOTHING else on
// this screen: everywhere else on the timeline below, every entry is
// something the AGENT decided and the ledger audited before acting. This
// button is the opposite — a human operator picks an entity, optionally
// types real customer details (or leaves them blank for the same
// auto-generated demo values the automated pipeline itself falls back to),
// and gets back a REAL Razorpay TEST-mode mandate registration link, live.
// It never touches check_bounds()/the gate — see api/main.py's route
// docstring for why that's correct, not an oversight.
// ---------------------------------------------------------------------------

function DemoConsolePanel({ rows }) {
  const invoiceRows = useMemo(
    () =>
      [...rows]
        .filter((r) => r.invoice_due != null)
        .sort((a, b) => a.entity_id.localeCompare(b.entity_id)),
    [rows],
  )

  const [entityId, setEntityId] = useState('')
  const activeId = entityId || invoiceRows[0]?.entity_id || ''
  const activeRow = invoiceRows.find((r) => r.entity_id === activeId)

  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [email, setEmail] = useState('')
  const [debitDate, setDebitDate] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const resetOutcome = () => {
    setResult(null)
    setError(null)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!activeId || submitting) return
    setSubmitting(true)
    setResult(null)
    setError(null)
    try {
      const body = {}
      if (name.trim()) body.customer_name = name.trim()
      if (contact.trim()) body.customer_contact = contact.trim()
      if (email.trim()) body.customer_email = email.trim()
      if (debitDate.trim()) body.debit_date = debitDate.trim()
      const res = await api.createMandateNow(activeId, body)
      setResult(res)
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="panel demo-console">
      <div className="demo-console-head">
        <span className="demo-console-badge">DEMO CONSOLE</span>
        <h2>Create Mandate Now</h2>
      </div>
      <p className="panel-sub">
        Manual operator action, not an agent decision: pick any invoice below and get back a REAL Razorpay TEST-mode
        mandate registration link immediately. This bypasses the funnel's guardrail/gate machinery on purpose — a
        human clicking this once is not the automated pipeline those bounds exist to constrain — but it still writes
        one audit entry, clearly labelled "manual demo", before returning anything.
      </p>

      {invoiceRows.length === 0 ? (
        <div className="empty-state">No invoice-backed entities loaded yet.</div>
      ) : (
        <form className="demo-console-form" onSubmit={handleSubmit}>
          <label className="demo-console-field">
            <span>Entity</span>
            <select
              value={activeId}
              onChange={(e) => {
                setEntityId(e.target.value)
                resetOutcome()
              }}
            >
              {invoiceRows.map((r) => (
                <option key={r.entity_id} value={r.entity_id}>
                  {r.entity_id} — {r.state}
                  {r.invoice_amount_inr != null ? ` — Rs.${r.invoice_amount_inr.toLocaleString('en-IN')}` : ''}
                </option>
              ))}
            </select>
          </label>

          <label className="demo-console-field">
            <span>Customer name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="leave blank for auto-generated demo values"
            />
          </label>

          <label className="demo-console-field">
            <span>Customer contact</span>
            <input
              value={contact}
              onChange={(e) => setContact(e.target.value)}
              placeholder="leave blank for auto-generated demo values"
            />
          </label>

          <label className="demo-console-field">
            <span>Customer email</span>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="leave blank for auto-generated demo values"
            />
          </label>

          <label className="demo-console-field">
            <span>Debit date</span>
            <input type="date" value={debitDate} onChange={(e) => setDebitDate(e.target.value)} />
            <span className="demo-console-hint">
              {activeRow?.invoice_due
                ? `leave blank to use the invoice's due date (${activeRow.invoice_due})`
                : "leave blank to use the invoice's due date"}
            </span>
          </label>

          <button type="submit" className="btn btn-demo-console" disabled={submitting || !activeId}>
            {submitting ? 'Creating…' : 'Create Real Mandate'}
          </button>
        </form>
      )}

      {error && (
        <div className="banner banner-error">
          Razorpay error: {error.message}
        </div>
      )}

      {result && (
        <div className="demo-console-result">
          <p>
            Mandate registration created — the debtor's registration link:{' '}
            <a href={result.subscription?.short_url} target="_blank" rel="noreferrer">
              {result.subscription?.short_url}
            </a>
          </p>
          <div className="demo-console-ids">
            <span>plan: {result.plan?.id}</span>
            <span>subscription: {result.subscription?.id}</span>
          </div>
          <p className="demo-console-hint">
            Contact on file for this debtor: <ContactSourceBadge source={result.contact_source} /> — any field you
            typed above overrode it for this one request; leaving a field blank uses whatever is resolved here.
          </p>
          <details className="timeline-detail">
            <summary>Customer fields actually sent</summary>
            <pre>{JSON.stringify(result.customer_used, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Reminders (packet P14) — real generated voice + real SMS.
//
// Read this next to DemoConsolePanel above, because the two are deliberately
// opposite. That one is an ungated operator console: it skips check_bounds()
// because a human clicking once to inspect a Razorpay object is not what the
// bounds exist to constrain. THIS one sends a real message to a debtor, so it
// spends that debtor's real weekly touch budget and the ledger can refuse it —
// and when it does, this panel shows the refusal in exactly the visual language
// the Human-Review screen already uses for a blocked approval. Same guardrail,
// same look, one mental model.
//
// REAL: the MP3 (gTTS, playable right here) and the SMS text.
// SIMULATED: the delivery — no phone is dialled, no handset is reached. Every
// row says so; the panel never implies otherwise.
// ---------------------------------------------------------------------------

const VOICE_NOTE_SRC = (url) => (url ? `/api${url}` : null)

const CHANNEL_LABEL = { voice: 'Voice reminder', sms: 'SMS reminder', message: 'WhatsApp/Message reminder' }

function ReminderRow({ row }) {
  const blocked = row.status === 'blocked'
  const failed = row.audio_generation === 'failed'
  const disabled = row.audio_generation === 'disabled'

  return (
    <li className={`review-item ${blocked ? 'review-item-legal' : ''}`}>
      <div className="review-item-head">
        <span className="review-entity">{CHANNEL_LABEL[row.channel] || row.channel}</span>
        <span className="review-kind">
          {blocked ? 'blocked by the guardrail' : row.manual ? 'sent — merchant triggered' : 'sent — agent escalation'}
        </span>
      </div>
      {!blocked && row.contact_phone && (
        <div className="review-reason">
          Addressed to: {row.contact_name || '—'} ({row.contact_phone}) <ContactSourceBadge source={row.contact_source} />
        </div>
      )}

      {blocked ? (
        <div className="review-warn">
          <strong>Blocked: {row.block_reason}</strong>
          <div>
            Nothing was sent. A manual reminder competes for the same weekly touch budget an autonomous nudge does,
            so <code>check_bounds()</code> refused it at click time — the same gate, and the same refusal, the
            Human-Review queue shows on a stale approval. This is the stopping rule working, not an error.
          </div>
        </div>
      ) : (
        <>
          <div className="review-summary">{row.text}</div>
          {row.channel === 'voice' && row.audio_url && (
            <audio className="reminder-audio" controls preload="none" src={VOICE_NOTE_SRC(row.audio_url)}>
              Your browser cannot play audio — the file is at <code>{row.audio_file}</code>.
            </audio>
          )}
          {row.channel === 'voice' && (failed || disabled) && (
            <div className="review-reason">
              {failed
                ? `No audio: text-to-speech failed (${row.audio_error}). The transcript above is still the real reminder — a network hiccup does not lose the message.`
                : 'No audio: TTS is switched off for this process (PK_REAL_TTS=0). Nothing was attempted — that is different from having tried and failed.'}
            </div>
          )}
          <div className="review-reason">
            {row.channel === 'voice' &&
              `Delivery: ${row.dial_status} — the audio is real and playable; no phone was dialled.`}
            {row.channel === 'sms' && `Delivery: ${row.send_status} — the text is real; it reached no handset.`}
            {row.channel === 'message' &&
              'Delivery: this rides the same simulated WhatsApp/email queue every message this system sends always ' +
                'has — no real WhatsApp Business or email credential is used. The text is real.'}
          </div>
        </>
      )}

      <div className="review-meta">
        <span>{fmtTs(row.ts)}</span>
        {row.audio_bytes ? <span>{Math.round(row.audio_bytes / 1024)} KB of real MP3</span> : null}
        {!blocked && <span>touch-counted against the weekly cap</span>}
      </div>
    </li>
  )
}

function RemindersPanel({ entityId }) {
  const { data, error, refetch } = usePolling(
    () => (entityId ? api.reminders(entityId) : Promise.resolve(null)),
    { intervalMs: 5000, deps: [entityId], enabled: Boolean(entityId) },
  )

  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  const send = async (channel) => {
    if (!entityId || busy) return
    setBusy(true)
    setNotice(null)
    try {
      const res = await api.remindNow(entityId, channel, text.trim() || null)
      // A refusal comes back on a 200 with blocked:true — the request was valid
      // and a bound stopped it. It gets the error tone (the merchant's click did
      // not achieve what they wanted) but it is not an exception.
      setNotice(
        res.blocked
          ? { kind: 'error', text: `${entityId}: refused by the guardrail — ${res.block_reason}` }
          : { kind: 'info', text: `${entityId}: ${channel} reminder sent (${res.action.id}).` },
      )
      if (!res.blocked) setText('')
    } catch (e) {
      setNotice({ kind: 'error', text: e.message })
    } finally {
      setBusy(false)
      refetch()
    }
  }

  const rows = (data && data.reminders) || []
  const counts = (data && data.counts) || {}

  return (
    <div className="panel">
      <h2>Reminders — voice, SMS &amp; WhatsApp/Message</h2>
      <p className="panel-sub">
        Send a real reminder now, or watch the guardrail refuse one. The voice note is genuinely generated audio
        (gTTS, Hinglish) you can play below, and the SMS/WhatsApp text is a real message — but <strong>nothing is
        actually delivered to a phone</strong>: there is no telephony, SMS-gateway, or WhatsApp Business credential in
        this project, so no phone rings, no SMS reaches a handset, and no WhatsApp message is placed. Every row says
        which half is which. Unlike the demo console further down, this button <strong>does</strong> go through{' '}
        <code>check_bounds()</code>: a manual reminder spends the same weekly touch budget an autonomous escalation
        does, and can be blocked exactly the same way.
      </p>

      {error && <div className="banner banner-error">Could not load reminders ({error.message}).</div>}
      {notice && <div className={`banner banner-${notice.kind === 'error' ? 'error' : 'info'}`}>{notice.text}</div>}

      <div className="reminder-compose">
        <label className="demo-console-field">
          <span>Custom message (optional — voice/SMS only)</span>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="leave blank to use the ledger's own template and amount"
          />
          <span className="demo-console-hint">
            Your words are spoken/sent verbatim on voice and SMS. They are never parsed for an amount or a date — the
            ledger decides those, never text typed into a box (CLAUDE.md law 2). The WhatsApp/Message button below
            always sends the ledger's own standard reminder text — it does not read this box.
          </span>
        </label>
        <div className="review-actions">
          <button type="button" className="btn btn-approve" disabled={busy || !entityId} onClick={() => send('voice')}>
            {busy ? 'Sending…' : 'Send Voice Reminder'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || !entityId} onClick={() => send('message')}>
            {busy ? 'Sending…' : 'Send WhatsApp/Message Reminder'}
          </button>
          <button type="button" className="btn btn-neutral" disabled={busy || !entityId} onClick={() => send('sms')}>
            {busy ? 'Sending…' : 'Send SMS Reminder'}
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="empty-state">
          <p>
            No reminders for <strong>{entityId || '—'}</strong> yet. The agent sends one on its own at escalation
            stage 2 when the touch budget allows; otherwise use the buttons above. Blocked attempts are listed here
            too — a refusal is a record, not a silence.
          </p>
        </div>
      ) : (
        <ul className="review-list">
          {rows.map((row, i) => (
            <ReminderRow key={`${row.status}-${row.action_id || row.ts}-${i}`} row={row} />
          ))}
        </ul>
      )}

      {rows.length > 0 && (
        <p className="panel-sub">
          {counts.sent || 0} sent · {counts.blocked || 0} refused by a bound.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Contacts (packet P15) — real name + phone identity, replacing the single
// synthetic demo contact (+919812345678) that every dispatch point used
// before an operator submits a real one for a debtor.
//
// NO REAL CALL, SMS, OR WHATSAPP MESSAGE IS EVER PLACED — not by this panel,
// not by anything else in this project. There is no telephony/SMS-gateway/
// WhatsApp-Business credential of any kind. A submitted number only changes
// (a) what the audit trail and this dashboard display from here on, and
// (b) the `customer.contact` field sent to the REAL Razorpay TEST API when a
// real payment link/mandate is created — Razorpay's sandbox genuinely reads
// that field. The disclaimer below says this in the UI itself, not only here.
//
// One row per entity the ledger knows about (`GET /contacts`), each editable
// in place and each carrying its own Voice/WhatsApp/SMS trigger buttons —
// "per-row", the shape the packet brief explicitly allows, rather than
// funnelling every entity through the single-entity picker the Reminders
// panel above already uses.
// ---------------------------------------------------------------------------

function ContactsPanel() {
  const { data, error, refetch } = usePolling(() => api.contacts(), { intervalMs: 6000 })
  const rows = useMemo(
    () => (Array.isArray(data) ? [...data].sort((a, b) => a.entity_id.localeCompare(b.entity_id)) : []),
    [data],
  )

  const [filter, setFilter] = useState('')
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(
      (r) =>
        r.entity_id.toLowerCase().includes(q) ||
        (r.debtor_id || '').toLowerCase().includes(q) ||
        (r.contact_name || '').toLowerCase().includes(q),
    )
  }, [rows, filter])

  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const [editPhone, setEditPhone] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [notice, setNotice] = useState(null)

  const startEdit = (row) => {
    setEditingId(row.entity_id)
    setEditName(row.contact_name || '')
    setEditPhone(row.contact_phone || '')
    setNotice(null)
  }

  const saveContact = async (entityId) => {
    if (!editName.trim() || !editPhone.trim() || busyId) return
    setBusyId(entityId)
    setNotice(null)
    try {
      const res = await api.submitContact(entityId, editName.trim(), editPhone.trim())
      const also = res.also_applies_to || []
      setNotice({
        kind: 'info',
        text:
          `${entityId}: real contact saved — ${res.contact.name} (${res.contact.phone}).` +
          (also.length ? ` Also now applies to: ${also.join(', ')}.` : ''),
      })
      setEditingId(null)
      refetch()
    } catch (e) {
      setNotice({ kind: 'error', text: e.body?.detail || e.message })
    } finally {
      setBusyId(null)
    }
  }

  const trigger = async (entityId, channel) => {
    if (busyId) return
    setBusyId(entityId)
    setNotice(null)
    try {
      const res = await api.remindNow(entityId, channel)
      setNotice(
        res.blocked
          ? { kind: 'error', text: `${entityId}: refused by the guardrail — ${res.block_reason}` }
          : {
              kind: 'info',
              text: `${entityId}: ${CHANNEL_LABEL[channel] || channel} dispatched (${res.action.id}). No real call, SMS, or WhatsApp message was placed — see the disclaimer above.`,
            },
      )
    } catch (e) {
      setNotice({ kind: 'error', text: e.message })
    } finally {
      setBusyId(null)
      refetch()
    }
  }

  return (
    <div className="panel contacts-panel">
      <div className="contacts-head">
        <span className="contacts-badge">REAL CONTACT DATA</span>
        <h2>Contacts</h2>
      </div>
      <p className="panel-sub">
        Attach a real name + phone number to a debtor or Scene-2 customer. Once submitted it replaces the single
        synthetic demo contact everywhere it was used for them — every sibling invoice they hold (shown below as
        "also applies to"), every voice/SMS/WhatsApp reminder, and the <code>customer.contact</code> field on any REAL
        Razorpay TEST-mode mandate/link created for them (see the Demo Console below).
      </p>
      <div className="contacts-disclaimer">
        No real phone call, SMS, or WhatsApp message is <strong>ever</strong> placed to a number entered here — this
        project holds no telephony, SMS-gateway, or WhatsApp Business credential. A submitted number only changes what
        the audit trail and this dashboard display, and the <code>customer.contact</code> field Razorpay's real TEST
        sandbox receives when a real mandate/link is created.
      </div>

      {error && <div className="banner banner-error">Could not load contacts ({error.message}).</div>}
      {notice && <div className={`banner banner-${notice.kind === 'error' ? 'error' : 'info'}`}>{notice.text}</div>}

      <input
        className="contacts-filter"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter by entity id, debtor id, or contact name…"
      />

      {filtered.length === 0 ? (
        <div className="empty-state">No entities match.</div>
      ) : (
        <div className="contacts-table-wrap">
          <table className="contacts-table">
            <thead>
              <tr>
                <th>Entity</th>
                <th>Debtor</th>
                <th>Amount</th>
                <th>State</th>
                <th>Contact</th>
                <th>Source</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => {
                const isEditing = editingId === row.entity_id
                const busy = busyId === row.entity_id
                return (
                  <tr key={row.entity_id} className={isEditing ? 'contacts-row-editing' : ''}>
                    <td>
                      <code>{row.entity_id}</code>
                    </td>
                    <td>{row.debtor_id || '—'}</td>
                    <td>
                      {row.invoice_amount_inr != null ? `Rs.${row.invoice_amount_inr.toLocaleString('en-IN')}` : '—'}
                    </td>
                    <td>{row.state}</td>
                    <td>
                      {isEditing ? (
                        <div className="contacts-edit-fields">
                          <input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Name" />
                          <input value={editPhone} onChange={(e) => setEditPhone(e.target.value)} placeholder="Phone" />
                        </div>
                      ) : (
                        <span>
                          {row.contact_name} — {row.contact_phone}
                        </span>
                      )}
                    </td>
                    <td>
                      <ContactSourceBadge source={row.contact_source} />
                    </td>
                    <td>
                      <div className="contacts-row-actions">
                        {isEditing ? (
                          <>
                            <button
                              type="button"
                              className="btn btn-tiny btn-contacts-save"
                              disabled={busy || !editName.trim() || !editPhone.trim()}
                              onClick={() => saveContact(row.entity_id)}
                            >
                              {busy ? 'Saving…' : 'Save'}
                            </button>
                            <button
                              type="button"
                              className="btn btn-tiny btn-neutral"
                              disabled={busy}
                              onClick={() => setEditingId(null)}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button type="button" className="btn btn-tiny btn-neutral" onClick={() => startEdit(row)}>
                            Edit contact
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn btn-tiny btn-approve"
                          disabled={busy}
                          onClick={() => trigger(row.entity_id, 'voice')}
                          title="Manual voice reminder — real generated audio, no phone dialled"
                        >
                          Voice
                        </button>
                        <button
                          type="button"
                          className="btn btn-tiny btn-neutral"
                          disabled={busy}
                          onClick={() => trigger(row.entity_id, 'message')}
                          title="Manual WhatsApp/email reminder — simulated queue, no real WhatsApp/email sent"
                        >
                          WhatsApp/Msg
                        </button>
                        <button
                          type="button"
                          className="btn btn-tiny btn-neutral"
                          disabled={busy}
                          onClick={() => trigger(row.entity_id, 'sms')}
                          title="Manual SMS reminder — real text, no handset reached"
                        >
                          SMS
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="contacts-summary-line">
        {rows.length} entities · {rows.filter((r) => r.contact_source === 'operator_submitted').length} with a real
        contact on file.
      </p>
    </div>
  )
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

      <ContactsPanel />

      <RemindersPanel entityId={activeId} />

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

      <DemoConsolePanel rows={rows} />
    </div>
  )
}
