// Hardcoded reference card — source of truth is engine/judgment/state_machine.py.
// These are named constants in that file (CLAUDE.md law #4: "All bounds live
// as constants at the top of state_machine.py"), not something the API
// serves, so mirroring them here as a static reference is intentional. If
// state_machine.py ever changes these values, update this file too.

export const NUMERIC_BOUNDS = [
  {
    name: 'MAX_TOUCHES_PER_WEEK',
    value: '2',
    detail: 'per debtor, rolling 7-day window',
  },
  {
    name: 'TOUCH_WINDOW_DAYS',
    value: '7',
    detail: 'the rolling window MAX_TOUCHES_PER_WEEK counts against',
  },
  {
    name: 'RENEGOTIATION_CAP',
    value: '2',
    detail: 'mandate re-offers after a broken promise, then no more',
  },
  {
    name: 'MANDATE_AMOUNT_CAP',
    value: 'Rs.1,00,000',
    detail: 'above this, falls back to partial + payment link',
  },
  {
    name: 'RETRY_ON_EXECUTION_FAILURE',
    value: '1',
    detail: 'one retry on mandate execution failure, then link/ladder/human',
  },
  {
    name: 'MAX_ESCALATE_STAGE',
    value: '4',
    detail: 'escalation ladder caps at stage 4, next failure forces HUMAN_HANDOFF',
  },
  {
    name: 'HARD_STEP_CAP',
    value: '60',
    detail: 'termination backstop — forces HUMAN_HANDOFF regardless of event content',
  },
]

export const POLICY_BOUNDS = [
  {
    name: 'dispute = instant stop',
    detail: 'a dispute_raised event moves any non-terminal entity straight to DISPUTED, no further outbound actions',
  },
  {
    name: 'no mandate re-offer after refusal',
    detail: 'once mandate_refused, that entity is never offered a mandate again',
  },
  {
    name: 'legal-stage notices -> merchant review',
    detail: 'the agent never sends legal communication itself; ESCALATE_3 legal-stage touches are blocked and routed to a human',
  },
]
