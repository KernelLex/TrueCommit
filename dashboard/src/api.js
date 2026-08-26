// Tiny fetch client for the Promise Keeper API + a polling hook. Every path
// goes through the Vite dev proxy at /api (see vite.config.js) which forwards
// to the FastAPI server on 127.0.0.1:8000 — the Python side has no CORS
// middleware on purpose, so this proxy is the only bridge.
//
// Every number the dashboard shows comes from one of these calls. Nothing in
// the UI layer invents a figure.

import { useCallback, useEffect, useRef, useState } from 'react'

const BASE = '/api'

class ApiError extends Error {
  constructor(message, status, body) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    })
  } catch (networkErr) {
    // API process not running / not reachable at all.
    throw new ApiError(networkErr.message || 'network error', 0, null)
  }

  let body = null
  const text = await res.text()
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!res.ok) {
    const detail = body && typeof body === 'object' && body.detail ? body.detail : res.statusText
    throw new ApiError(detail || `HTTP ${res.status}`, res.status, body)
  }
  return body
}

export const api = {
  health: () => request('/health'),
  entities: () => request('/entities'),
  entity: (id) => request(`/entities/${encodeURIComponent(id)}`),
  entityAudit: (id) => request(`/entities/${encodeURIComponent(id)}/audit`),
  audit: (limit = 200) => request(`/audit?limit=${limit}`),
  trustList: () => request('/trust'),
  trust: (debtorId) => request(`/trust/${encodeURIComponent(debtorId)}`),
  // Does not exist yet (a later packet adds it) — callers must expect this
  // to reject and handle it gracefully (see App.jsx's time-warp handlers).
  advance: (days) => request('/advance', { method: 'POST', body: JSON.stringify({ days }) }),
  // Packet P6: config/agents.yaml surface + live provider/sentinel/cache
  // status — everything the System Health "Agent parameters" and live
  // status cards render comes from this one call.
  config: () => request('/config'),
  world: () => request('/world'),

  // ---- Packet P10: the Day Story surface --------------------------------
  // All five are READ-ONLY lenses on data the engine already computed and
  // audited. `guardrailChecks` in particular is a preview, not an action: it
  // creates nothing and writes no audit entry (there is a test).
  debtors: () => request('/debtors'),
  conversation: (id) => request(`/entities/${encodeURIComponent(id)}/conversation`),
  guardrailChecks: (id, { actionKind, stage } = {}) => {
    const query = new URLSearchParams()
    if (actionKind) query.set('action_kind', actionKind)
    if (stage) query.set('stage', stage)
    const suffix = query.toString() ? `?${query}` : ''
    return request(`/entities/${encodeURIComponent(id)}/guardrail-checks${suffix}`)
  },
  mandateTimeline: (id) => request(`/entities/${encodeURIComponent(id)}/mandate-timeline`),
  dayStory: (day) => request(`/day/${Number(day)}/story`),

  // ---- Packet P9: the human-review queue -------------------------------
  // Every call below is a HUMAN acting. None of them decides anything: the
  // ledger audits the click before it takes effect and re-runs check_bounds()
  // on anything it is asked to send, so an approve can legitimately come back
  // { blocked: true } and the screen has to show that rather than assume the
  // click worked.
  reviewQueue: () => request('/review-queue'),
  approveHeld: (id) => request(`/review-queue/${encodeURIComponent(id)}/approve`, { method: 'POST' }),
  rejectHeld: (id) => request(`/review-queue/${encodeURIComponent(id)}/reject`, { method: 'POST' }),
  markHeldHandled: (id, note) =>
    request(`/review-queue/${encodeURIComponent(id)}/mark-handled`, {
      method: 'POST',
      body: JSON.stringify({ note: note || null }),
    }),
  resolveHandoff: (entityId, resolution) =>
    request(`/entities/${encodeURIComponent(entityId)}/resolve-handoff`, {
      method: 'POST',
      body: JSON.stringify({ resolution }),
    }),
  pauseEntity: (entityId) => request(`/entities/${encodeURIComponent(entityId)}/pause`, { method: 'POST' }),
  unpauseEntity: (entityId) => request(`/entities/${encodeURIComponent(entityId)}/unpause`, { method: 'POST' }),

  // ---- Packet P13: Demo Console ------------------------------------------
  // NOT an agent action — a human operator's one-click button that creates a
  // REAL Razorpay TEST-mode mandate registration immediately, for
  // inspection. Never goes through the funnel/gate machinery the time-warp
  // clock uses. `body` is `{customer_name?, customer_contact?, customer_email?,
  // debit_date?}`, all optional — see api/main.py's CreateMandateNowIn.
  createMandateNow: (entityId, body) =>
    request(`/entities/${encodeURIComponent(entityId)}/create-mandate-now`, {
      method: 'POST',
      body: JSON.stringify(body || {}),
    }),

  // ---- Packet P14: real voice + SMS reminders ----------------------------
  // The OPPOSITE shape to the Demo Console above, and the difference matters.
  // `remindNow` sends a real message to a debtor, so it spends that debtor's
  // real weekly touch budget and the ledger can refuse it: like `approveHeld`,
  // it resolves with { blocked: true, block_reason } on an HTTP 200 rather than
  // throwing, and the UI has to render that refusal instead of assuming the
  // click worked. Audio URLs come back as `/voice-notes/<file>.mp3`; prefix
  // them with `/api` (see VOICE_NOTE_SRC in EntityTimelineScreen) to play them
  // through the dev proxy.
  remindNow: (entityId, channel, customText) =>
    request(`/entities/${encodeURIComponent(entityId)}/remind-now`, {
      method: 'POST',
      body: JSON.stringify({ channel, custom_text: customText || null }),
    }),
  reminders: (entityId) => request(`/entities/${encodeURIComponent(entityId)}/reminders`),
}

export { ApiError }

/**
 * Polls `fetcher` every `intervalMs` and keeps { data, error, loading }
 * current. Re-subscribes when anything in `deps` changes. A failed poll
 * keeps the last good `data` on screen (never blanks the UI) but surfaces
 * the error so callers can show a status indicator.
 */
export function usePolling(fetcher, { intervalMs = 3000, deps = [], enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const refetch = useCallback(async () => {
    try {
      const result = await fetcherRef.current()
      setData(result)
      setError(null)
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return undefined
    }
    let cancelled = false
    let timer
    setLoading(true)

    async function tick() {
      if (cancelled) return
      await refetch()
      if (!cancelled) timer = setTimeout(tick, intervalMs)
    }
    tick()

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, ...deps])

  return { data, error, loading, refetch }
}
