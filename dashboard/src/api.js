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
