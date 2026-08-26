// Day Story (packet P10) — "press Advance-Day, then SEE what happened".
//
// Every value on this screen comes out of one of two calls: GET /day/{n}/story
// and GET /entities/{id}/mandate-timeline. Nothing is computed here beyond
// picking a colour and a label from a field the API already sent. Where the API
// sends null it says why (a cart customer with no stored name, a debtor with no
// trust posterior yet) and this screen prints that sentence rather than a dash
// or a plausible substitute — CLAUDE.md law 8 applies to pixels too.
//
// The real/simulated line is drawn by the API's `nature` field, never inferred
// here: registration links can be genuinely real Razorpay TEST-mode objects,
// mandate EXECUTION never is (tracking/TRACK_BAR.md §0), and the two must never
// wear the same badge.

import { useEffect, useState } from 'react'
import { api } from '../api'
import BetaCurve from '../components/BetaCurve'

const NATURE_BADGE = {
  engine_decision: { label: 'AUDITED', className: 'nature-engine' },
  razorpay_real: { label: 'REAL RAZORPAY', className: 'nature-real' },
  razorpay_simulated: { label: 'SIMULATED', className: 'nature-sim' },
  simulated_persona: { label: 'SIMULATED PERSONA', className: 'nature-sim' },
  simulated_outcome: { label: 'SIMULATED', className: 'nature-sim' },
}

const LAYER_LABEL = {
  perception: 'Perception',
  judgment: 'Judgment',
  action: 'Action',
  sentinel: 'Sentinel',
  auditor: 'Auditor',
}

function fmtInr(n) {
  return typeof n === 'number' ? `Rs.${n.toLocaleString('en-IN')}` : null
}

/** Numeric trust mean + the same Beta curve the Trust Curves screen draws,
 *  shrunk to card size. Both numbers come from the day's snapshot, so this is
 *  the posterior as it stood THAT day, not today's. */
function TrustPip({ trust, note }) {
  if (!trust) {
    return <div className="story-trust story-trust-empty">{note || 'no trust posterior recorded'}</div>
  }
  return (
    <div className="story-trust">
      <div className="story-trust-head">
        <span className="story-trust-mean">{trust.mean.toFixed(2)}</span>
        <span className="story-trust-label">trust, end of day {trust.as_of_day}</span>
      </div>
      <BetaCurve alpha={trust.alpha} beta={trust.beta} width={132} height={38} />
      <div className="story-trust-ab">
        α {trust.alpha.toFixed(2)} · β {trust.beta.toFixed(2)}
      </div>
    </div>
  )
}

function NatureBadge({ nature }) {
  const badge = NATURE_BADGE[nature]
  if (!badge) return null
  return <span className={`nature-badge ${badge.className}`}>{badge.label}</span>
}

/** The per-decision checklist. Deliberately NOT a redesigned summary: these are
 *  the exact per-constant results the ledger recorded when the decision was
 *  made, detail strings and all. */
function GuardrailPanel({ guardrail }) {
  const blocked = guardrail.status === 'blocked'
  return (
    <details className={`guardrail ${blocked ? 'guardrail-blocked' : 'guardrail-allowed'}`}>
      <summary>
        <span className="guardrail-status">{blocked ? '✗ Blocked' : '✓ Allowed'}</span>
        <span className="guardrail-kind">{guardrail.kind}</span>
        <span className="guardrail-count">
          {guardrail.passed}/{guardrail.total} guardrails passed
        </span>
      </summary>
      {blocked && guardrail.audited_reason && (
        <div className="guardrail-reason">Audit trail reason: “{guardrail.audited_reason}”</div>
      )}
      <ul className="guardrail-list">
        {guardrail.checks.map((check) => (
          <li key={check.name} className={check.passed ? 'check-pass' : 'check-fail'}>
            <span className="check-icon">{check.passed ? '✓' : '✗'}</span>
            <span className="check-name">{check.name}</span>
            <span className="check-detail">{check.detail}</span>
          </li>
        ))}
      </ul>
      <div className="guardrail-foot">
        {Object.keys(guardrail.params || {}).length > 0 && (
          <span>params: {JSON.stringify(guardrail.params)}</span>
        )}
        <span>{guardrail.source}</span>
      </div>
    </details>
  )
}

function MessageBeat({ beat }) {
  const outbound = beat.direction === 'out'
  return (
    <div className={`chat-row ${outbound ? 'chat-row-out' : 'chat-row-in'}`}>
      <div className={`chat-bubble ${outbound ? 'chat-bubble-out' : 'chat-bubble-in'}`}>
        <div className="chat-meta">
          <span>{outbound ? 'Agent' : 'Debtor'}</span>
          <span>· {beat.channel}</span>
          {beat.origin === 'dataset' && <span className="chat-origin">· dataset thread history</span>}
        </div>
        <div className="chat-text">{beat.text}</div>
        {beat.summary && <div className="chat-audit">{beat.summary}</div>}
      </div>
    </div>
  )
}

function AuditBeat({ beat }) {
  return (
    <div className="story-beat">
      <span className={`timeline-badge timeline-badge-${beat.layer}`}>
        {LAYER_LABEL[beat.layer] || beat.layer}
      </span>
      <div className="story-beat-body">
        <div className="story-beat-summary">{beat.summary}</div>
        {beat.guardrail_summary && <GuardrailPanel guardrail={beat.guardrail_summary} />}
        <details className="timeline-detail">
          <summary>Raw audit detail ({beat.audit_id})</summary>
          <pre>{JSON.stringify(beat.detail, null, 2)}</pre>
        </details>
      </div>
    </div>
  )
}

/** Created → Registered → Debtor response → Executed/Failed → Revoked, straight
 *  off the audit trail. The account-gate note sits next to the Registered step
 *  because that is exactly where the real rail stops in this sandbox. */
function MandateStepper({ timeline }) {
  if (!timeline || !timeline.steps || timeline.steps.length === 0) return null
  return (
    <div className="mandate-stepper">
      <div className="mandate-head">
        <h4>Mandate lifecycle</h4>
        <span className="mandate-status">
          {timeline.status} · the full lifecycle across every day, not only this one
        </span>
      </div>
      <ol className="mandate-steps">
        {timeline.steps.map((step) => (
          <li key={step.audit_id} className={`mandate-step mandate-step-${step.nature}`}>
            <div className="mandate-step-head">
              <span className="mandate-step-label">{step.label}</span>
              <NatureBadge nature={step.nature} />
              <span className="mandate-step-day">day {step.day}</span>
            </div>
            <div className="mandate-step-summary">{step.summary}</div>
            {step.detail.short_url &&
              (step.real ? (
                <a className="mandate-link" href={step.detail.short_url} target="_blank" rel="noreferrer">
                  {step.detail.short_url}
                </a>
              ) : (
                <span className="mandate-link mandate-link-sim">{step.detail.short_url}</span>
              ))}
            {step.gate_note && <div className="mandate-gate-note">{step.gate_note}</div>}
            {step.detail.move && <div className="mandate-step-extra">persona move: {step.detail.move}</div>}
            {step.detail.reason && <div className="mandate-step-extra">reason: {step.detail.reason}</div>}
            {step.detail.fallback_reason && (
              <div className="mandate-step-extra">fell back because: {step.detail.fallback_reason}</div>
            )}
            {typeof step.detail.amount_inr === 'number' && (
              <div className="mandate-step-extra">amount: {fmtInr(step.detail.amount_inr)}</div>
            )}
          </li>
        ))}
      </ol>
      <p className="mandate-foot">{timeline.lifecycle_note}</p>
    </div>
  )
}

function EntityCard({ block, timeline }) {
  const messages = block.beats.filter((b) => b.type === 'message')
  const audits = block.beats.filter((b) => b.type === 'audit')

  return (
    <section className="story-card">
      <header className="story-card-head">
        <div className="story-identity">
          <h2 className="story-debtor">{block.debtor_label}</h2>
          <div className="story-ids">
            <span>{block.entity_id}</span>
            {block.debtor_id && <span>· {block.debtor_id}</span>}
            {block.invoice_amount_inr != null && <span>· {fmtInr(block.invoice_amount_inr)}</span>}
            {block.state_end_of_day && (
              <span className={`state-pill state-pill-${block.state_end_of_day}`}>
                {block.state_end_of_day}
              </span>
            )}
          </div>
          {block.debtor_name_note && <div className="story-name-note">{block.debtor_name_note}</div>}
        </div>
        <TrustPip trust={block.trust} note={block.trust_note} />
      </header>

      <div className="story-card-stats">
        <span>{block.counts.beats} beats</span>
        <span>{block.counts.messages} messages</span>
        <span>{block.counts.guardrail_checks} guardrail checks</span>
        {block.counts.blocks > 0 && <span className="story-stat-block">{block.counts.blocks} blocked</span>}
        {block.paused && <span className="story-stat-block">paused by merchant</span>}
      </div>

      {messages.length > 0 && (
        <div className="story-section">
          <h3>Conversation</h3>
          <div className="chat">
            {messages.map((beat) => (
              <MessageBeat key={beat.message_id} beat={beat} />
            ))}
          </div>
        </div>
      )}

      <MandateStepper timeline={timeline} />

      {audits.length > 0 && (
        <div className="story-section">
          <h3>Decisions &amp; guardrails</h3>
          <div className="story-beats">
            {audits.map((beat) => (
              <AuditBeat key={beat.audit_id} beat={beat} />
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

export default function DayStoryScreen({ dayHint }) {
  const [day, setDay] = useState(null)
  const [reloadToken, setReloadToken] = useState(0)
  // One object so a finished fetch lands atomically: a story never renders
  // beside another day's mandate timelines.
  const [loaded, setLoaded] = useState({ story: null, timelines: {}, error: null })

  // React's "adjust state when a prop changes" pattern, done during render
  // rather than in an effect: the parent hands us a day right after a
  // successful /advance, and after that the user's own selection sticks.
  const [seenHint, setSeenHint] = useState(dayHint)
  if (dayHint !== seenHint) {
    setSeenHint(dayHint)
    if (typeof dayHint === 'number') setDay(dayHint)
  }

  // No day chosen yet -> open on the last day that actually ran. `world.day`
  // is days ELAPSED, so the last simulated index is one less.
  useEffect(() => {
    if (day !== null) return undefined
    let cancelled = false
    api
      .world()
      .then((w) => {
        if (!cancelled) setDay(Math.max(0, w.day - 1))
      })
      .catch((e) => {
        if (!cancelled) setLoaded((prev) => ({ ...prev, error: e }))
      })
    return () => {
      cancelled = true
    }
  }, [day])

  useEffect(() => {
    if (day === null) return undefined
    let cancelled = false
    const run = async () => {
      try {
        const body = await api.dayStory(day)
        if (cancelled) return
        // Only entities whose day actually touched a mandate need the
        // lifecycle call — no speculative fan-out across the whole day.
        const fetched = await Promise.all(
          body.entities
            .filter((e) => e.has_mandate_activity)
            .map((e) =>
              api
                .mandateTimeline(e.entity_id)
                .then((t) => [e.entity_id, t])
                .catch(() => [e.entity_id, null]),
            ),
        )
        if (!cancelled) setLoaded({ story: body, timelines: Object.fromEntries(fetched), error: null })
      } catch (e) {
        if (!cancelled) setLoaded((prev) => ({ ...prev, error: e }))
      }
    }
    run()
    return () => {
      cancelled = true
    }
  }, [day, reloadToken])

  const { story, timelines, error } = loaded
  const showing = story !== null && story.day === day
  const worldDay = story ? story.world_day : null
  const step = (delta) => setDay((d) => Math.max(0, (d ?? 0) + delta))

  return (
    <div className="screen">
      <div className="screen-head">
        <h1>Day Story</h1>
      </div>
      <p className="screen-sub">
        What actually happened on one simulated day, per customer: the real conversation, the decisions as they were
        audited, and — for every money decision — the guardrail checklist recorded at the instant it was made. Days are
        0-based, the way the runner counts them: <code>advance(1)</code> simulates day 0, so the day you just watched is
        always the world day minus one.
      </p>

      <div className="day-picker">
        <button type="button" className="btn btn-neutral" onClick={() => step(-1)} disabled={(day ?? 0) <= 0}>
          ◀ Prev
        </button>
        <label htmlFor="day-input">Day</label>
        <input
          id="day-input"
          type="number"
          min="0"
          value={day ?? ''}
          onChange={(e) => {
            const next = Number.parseInt(e.target.value, 10)
            setDay(Number.isNaN(next) ? 0 : Math.max(0, next))
          }}
        />
        <button type="button" className="btn btn-neutral" onClick={() => step(1)}>
          Next ▶
        </button>
        {showing && (
          <span className="day-picker-meta">
            {story.date} · world is at day {worldDay}
            {story.simulated ? '' : ' · not simulated yet'}
          </span>
        )}
        <button type="button" className="btn btn-neutral" onClick={() => setReloadToken((n) => n + 1)}>
          Reload
        </button>
      </div>

      {error && <div className="banner banner-error">Could not load the day story ({error.message}).</div>}

      {showing && (
        <div className="story-summary-row">
          <span>
            <strong>{story.counts.entities}</strong> customers active
          </span>
          <span>
            <strong>{story.counts.messages}</strong> messages
          </span>
          <span>
            <strong>{story.counts.beats}</strong> beats
          </span>
          <span>
            <strong>{story.counts.blocks}</strong> actions stopped by a guardrail
          </span>
        </div>
      )}

      {showing && story.entities.length === 0 && !error && (
        <div className="empty-state">
          <p>{story.status}</p>
          {story.simulated && (
            <p className="empty-note">
              A quiet day is a real outcome, not a gap: the touch cadence only fires on scheduled days, and a debtor
              with a live promise is deliberately left alone until it comes due.
            </p>
          )}
        </div>
      )}

      {!showing && !error && <div className="empty-state">Loading day {day ?? '…'}…</div>}

      {showing &&
        story.entities.map((block) => (
          <EntityCard key={block.entity_id} block={block} timeline={timelines[block.entity_id]} />
        ))}

      {showing && story.entities.length > 0 && (
        <p className="empty-note story-legend">
          <strong>AUDITED</strong> = a real decision this system made and wrote to the append-only trail ·{' '}
          <strong>REAL RAZORPAY</strong> = a live TEST-mode API call with a real hosted URL ·{' '}
          <strong>SIMULATED</strong> = this build's stand-in. Mandate execution is simulated in every run:{' '}
          {story.notes.mandate_lifecycle}
        </p>
      )}
    </div>
  )
}
