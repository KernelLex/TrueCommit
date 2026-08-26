"""Razorpay TEST-mode client — REAL for the sandbox-verified endpoints,
SIMULATED (clearly labeled) only for the two mandate-lifecycle calls this
build's AUTOMATED pipeline cannot run against real calendar time.

Sandbox verification (CLAUDE.md Day-1 priority #1, `scripts/verify_razorpay_sandbox.py`
-> `tracking/razorpay_sandbox_report.json`, 8/8 probes green, `tracking/TRACK_BAR.md`
section 0, verified 2026-08-26) found REAL in TEST mode:
  - POST /payment_links          -> create_payment_link
  - POST /invoices               -> create_invoice
  - POST /customers               -> create_customer
  - POST /subscription_registration/auth_links -> create_mandate_registration_link
    (registration only — real, but the ALTERNATE mandate rail as of the
    2026-08-26 pivot below; UPI Autopay stays account-gated)

MANDATE RAIL PIVOT (2026-08-26, packet P12 — full narrative in
tracking/BUILD_LOG.md's "mandate rail pivot" entry, decision recorded in
tracking/DECISIONS.md, status in tracking/TRACK_BAR.md section 0): the
PRIMARY / default mandate-registration method is now
`create_mandate_via_subscription` (POST /plans + POST /subscriptions,
method="emandate" via netbanking), because it is the one Razorpay surface
on this account where the FULL lifecycle was proven genuinely real, human
-verified, end to end — not just registration:
  Plan `plan_TULfhYrG9rmMjR` -> Subscription `sub_TULfqScOEmQ57p`
  (total_count=1) -> authorized via netbanking eMandate, captured payment
  `pay_TULmn2CWCOuWDu` with real recurring token `token_TULmXon2Xf7bco` ->
  real token revoke, confirmed gone (`{"deleted": true}`, re-lookup 400).
  Future-dated scheduling (`start_at` 7 days out, matching a real invoice
  due date) verified separately as `sub_TUM5ilVyr8rpZZ`.
`check_mandate_execution` (a QUERY — Razorpay's own billing engine executes
the charge, there is no execute API call in this flow) and
`revoke_mandate_token` (a REAL DELETE) complete that lifecycle. UPI Autopay
remains account-gated ("raise a request"); `create_mandate_registration_link`
is KEPT, unchanged, as the alternate rail in case that gate ever lifts.

STILL SIMULATED, and why that's a scheduling constraint now, not a
capability gap (execute_mandate / revoke_mandate always return a payload
with a literal `"simulated": True` field + a `reason` — see each method's
docstring):
  - execute_mandate  — the AUTOMATED pipeline's simulator advances 45
    virtual days in seconds; Razorpay's real billing engine auto-charges on
    a real `charge_at` and cannot be sped up. Wiring the proven-real
    Subscriptions rail into that loop is out of scope for packet P12 — see
    check_mandate_execution for the real, non-fabricating query instead.
  - revoke_mandate   — same automated-loop constraint; see
    revoke_mandate_token for the real DELETE, used when a token genuinely
    exists to revoke against.

KNOWN GOTCHA (tracking/BUILD_LOG.md, 2026-08-26): `POST /v1/orders` accepts a
`token: {...}` block with HTTP 200 but silently drops it (fetched back as
`token: null`). Do NOT build mandates from hand-rolled token orders — use
`subscription_registration/auth_links` (alternate rail) or
`/plans`+`/subscriptions` (primary rail), never a hand-rolled token order.

KNOWN QUIRK (tracking/BUILD_LOG.md, 2026-08-26): `subscription.status` lags
behind the real payment record — it stayed `"created"` even after a real
capture in the live verification session. Never trust the subscription's
own `status` field to mean "charged"; `check_mandate_execution` deliberately
never reads it, checking for a captured payment instead.

AMOUNT LAW (CLAUDE.md law 2 / BUILD.md ground rule 2): every `*_inr` argument
below is copied verbatim from a ledger record by the caller (the judgment
layer) — this module never computes, guesses, or interpolates an amount. The
ONLY arithmetic performed on any amount anywhere in this file is the INR ->
paise conversion (`amount_inr * PAISE_PER_INR`), applied once per amount,
right where it enters an API payload. (The registration link's top-level
`amount: 100` is a separate, fixed protocol minimum Razorpay's UPI Autopay
registration requires for the initial nominal charge — not a business amount
and not derived from any caller argument; the actual mandate ceiling the
caller controls is `max_amount`, built from `max_amount_inr` via the same
paise conversion. See create_mandate_registration_link's docstring.)
`create_mandate_via_subscription` adds exactly one more conversion,
`debit_date` ("YYYY-MM-DD") -> `start_at` (unix seconds) — a CALENDAR
conversion, not a money computation, using the same pattern already used by
create_invoice's `due_date` -> `expire_by`. It is loudly commented at its
one call site.

KEY LAW (CLAUDE.md hard rule 1): keys are loaded lazily from `.env` via
python-dotenv, only inside `RazorpayClient.__init__` — importing this module
never requires the keys to exist. `RazorpayClient` refuses to construct
(raises RazorpayError) unless the key id starts with "rzp_test_", making it
structurally impossible to point this client at a live key.
"""

import datetime as dt
import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

BASE_URL = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 30.0
PAISE_PER_INR = 100  # the ONLY factor used to convert an INR amount to paise, anywhere in this module
MANDATE_REGISTRATION_WINDOW_SECONDS = 60 * 60 * 24 * 30  # 30 days, matches the verified probe
UPI_AUTOPAY_MIN_INITIAL_CHARGE_PAISE = 100  # Rs.1 — Razorpay rejects amount=0 (BUILD_LOG 2026-08-26)


class RazorpayError(Exception):
    """Raised on any non-2xx Razorpay response, and on client-construction
    refusal (missing/malformed keys). One exception type so upstream
    Sentinel/dead-letter machinery (engine/action/sentinel.py) can catch it
    without knowing Razorpay's JSON error shape."""

    def __init__(self, message: str, status_code: int | None = None, description: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.description = description


def _to_paise(amount_inr: int) -> int:
    """The one and only amount transformation this module performs."""
    return amount_inr * PAISE_PER_INR


class RazorpayClient:
    """Thin REST wrapper over the Razorpay TEST-mode API (basic auth,
    base https://api.razorpay.com/v1, 30s timeout). Env is loaded lazily at
    construction time, never at import time.

    `transport` is exposed only so tests can inject `httpx.MockTransport` —
    production callers never pass it.
    """

    def __init__(
        self,
        key_id: str | None = None,
        key_secret: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if key_id is None or key_secret is None:
            load_dotenv()
            key_id = key_id if key_id is not None else os.environ.get("RAZORPAY_TEST_KEY_ID", "")
            key_secret = key_secret if key_secret is not None else os.environ.get("RAZORPAY_TEST_KEY_SECRET", "")
        if not key_id or not key_secret:
            raise RazorpayError(
                "RAZORPAY_TEST_KEY_ID / RAZORPAY_TEST_KEY_SECRET not set - add them to .env"
            )
        if not key_id.startswith("rzp_test_"):
            raise RazorpayError(
                "Refusing to construct RazorpayClient: key_id does not start with "
                "'rzp_test_' - this client structurally refuses live keys"
            )
        self._client = httpx.Client(
            base_url=BASE_URL,
            auth=(key_id, key_secret),
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RazorpayClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- transport --------------------------------------------------------

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(path, json=payload)
        return self._handle(resp)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._client.get(path, params=params)
        return self._handle(resp)

    def _delete(self, path: str) -> dict[str, Any]:
        resp = self._client.delete(path)
        return self._handle(resp)

    @staticmethod
    def _handle(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except Exception:
            body = {}
        if resp.status_code >= 300:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            description = err.get("description") or (resp.text[:300] if resp.text else "no response body")
            raise RazorpayError(
                f"Razorpay API error {resp.status_code}: {description}",
                status_code=resp.status_code,
                description=description,
            )
        return body

    # -- REAL: verified sandbox endpoints ----------------------------------

    def create_payment_link(self, amount_inr: int, description: str, customer: dict[str, str]) -> dict[str, Any]:
        """POST /payment_links — REAL in Razorpay TEST mode (verified
        2026-08-26). `customer` is passed through untouched, e.g.
        {"name":..., "contact":..., "email":...}. Returns the created link's
        full response, including `id` and `short_url`."""
        payload = {
            "amount": _to_paise(amount_inr),
            "currency": "INR",
            "description": description,
            "customer": customer,
            "notify": {"sms": False, "email": False},
        }
        return self._post("/payment_links", payload)

    def create_invoice(
        self, amount_inr: int, description: str, customer: dict[str, str], due_date: str,
    ) -> dict[str, Any]:
        """POST /invoices — REAL in Razorpay TEST mode (verified 2026-08-26).
        type="invoice" with a single line_item carrying the full amount,
        mirroring the verified probe exactly.

        `due_date` ("YYYY-MM-DD") is sent as `expire_by` (unix seconds,
        midnight UTC) — Razorpay's invoice due/expiry field. Judgment call:
        the Day-1 probe didn't itself pass `expire_by` (invoices don't
        require it), so this one extra field on an otherwise-verified-real
        endpoint hasn't been probed byte-for-byte — if Razorpay ever rejected
        it the POST would fail loudly with RazorpayError, never silently.
        """
        expire_by = int(
            dt.datetime.strptime(due_date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
        )
        payload = {
            "type": "invoice",
            "description": description,
            "customer": customer,
            "line_items": [
                {"name": description, "amount": _to_paise(amount_inr), "currency": "INR", "quantity": 1}
            ],
            "expire_by": expire_by,
        }
        return self._post("/invoices", payload)

    def create_customer(self, name: str, contact: str, email: str) -> dict[str, Any]:
        """POST /customers — REAL in Razorpay TEST mode (verified 2026-08-26).
        `fail_existing="0"` makes a repeat call for the same contact/email
        return the existing customer instead of erroring."""
        payload = {"name": name, "contact": contact, "email": email, "fail_existing": "0"}
        return self._post("/customers", payload)

    def create_mandate_registration_link(
        self,
        max_amount_inr: int,
        description: str,
        customer: dict[str, str],
        method: str = "upi",
    ) -> dict[str, Any]:
        """POST /subscription_registration/auth_links — REAL in Razorpay TEST
        mode (verified 2026-08-26; tracking/TRACK_BAR.md section 0). It
        returns a hosted registration `short_url` a human authorizes once in
        a browser/UPI app, producing a token a real recurring charge would
        run against.

        ALTERNATE RAIL as of the 2026-08-26 mandate rail pivot (packet P12 —
        see this module's top docstring, tracking/BUILD_LOG.md's "mandate
        rail pivot" entry, and tracking/TRACK_BAR.md section 0). This method
        is KEPT here UNCHANGED, not deleted and not functionally modified:
        UPI Autopay stays account-gated ("raise a request", never pursued
        further per explicit instruction), so the primary mandate path is
        now `create_mandate_via_subscription` (POST /plans + /subscriptions),
        the one rail proven real end to end including execute and revoke.
        This method remains the live option for method="emandate" via a
        registration LINK (as opposed to a Subscription) in case UPI
        enablement ever lands and that product shape becomes preferable
        again — registration-only, same as it always was; it was never the
        piece of this rail that changed.

        method="upi" (UPI Autopay): requires a nonzero initial `amount`
        (BUILD_LOG 2026-08-26 — 0 is rejected: "Order amount less than
        minimum amount allowed"; Rs.1 / 100 paise works, so that fixed
        protocol minimum is what's sent — see the module docstring's AMOUNT
        LAW note on why this isn't a caller-controlled amount) and
        `frequency="as_presented"`, both exactly as the verified probe sent.

        method="emandate" (eNACH/bank account): `auth_type="netbanking"`, no
        `frequency` field — the verified probe omits it for this method too.

        `max_amount_inr` is the actual mandate ceiling the caller controls
        (already bounds-checked upstream against MANDATE_AMOUNT_CAP); the
        only arithmetic applied to it is the same INR -> paise conversion
        used everywhere else, producing `max_amount`.

        Returns the full response, including `id` and `short_url`.
        """
        if method not in ("upi", "emandate"):
            raise RazorpayError(
                f"Unsupported mandate registration method: {method!r} (expected 'upi' or 'emandate')"
            )

        expire_at = int(time.time()) + MANDATE_REGISTRATION_WINDOW_SECONDS

        subscription_registration: dict[str, Any] = {
            "method": method,
            "max_amount": _to_paise(max_amount_inr),
            "expire_at": expire_at,
        }
        if method == "upi":
            subscription_registration["frequency"] = "as_presented"
        else:  # emandate
            subscription_registration["auth_type"] = "netbanking"

        payload = {
            "customer": customer,
            "type": "link",
            "amount": UPI_AUTOPAY_MIN_INITIAL_CHARGE_PAISE,
            "currency": "INR",
            "description": description,
            "subscription_registration": subscription_registration,
        }
        return self._post("/subscription_registration/auth_links", payload)

    # -- REAL: mandate lifecycle via Subscriptions (PRIMARY rail, packet P12) --
    # Live-verified end to end 2026-08-26 (tracking/BUILD_LOG.md "mandate rail
    # pivot" entry, real object IDs cited in this module's top docstring).

    def create_plan(self, amount_inr: int, description: str) -> dict[str, Any]:
        """POST /plans — REAL in Razorpay TEST mode, live-verified
        2026-08-26 (`plan_TULfhYrG9rmMjR`). First step of
        create_mandate_via_subscription — see that method's docstring for
        why `period: "monthly"` is required schema plumbing, not a real
        recurrence (`total_count: 1` on the subscription that follows is
        what actually makes the resulting mandate one-time). Returns the
        full response, including `id`.
        """
        payload = {
            # REQUIRED by Razorpay's plan schema even though we only ever
            # bill once — it is NOT a real recurrence. `total_count: 1` on
            # the subscription (see create_mandate_via_subscription) is what
            # makes this a one-time debit. Do not "fix" this by removing it
            # or trying to express "one-time" here — there is no such value
            # for `period`, and this field isn't claiming a monthly cadence.
            "period": "monthly",
            "interval": 1,
            "item": {
                "name": description,
                "amount": _to_paise(amount_inr),
                "currency": "INR",
                "description": description,
            },
        }
        return self._post("/plans", payload)

    def create_mandate_via_subscription(
        self,
        amount_inr: int,
        description: str,
        customer: dict[str, str],
        debit_date: str,
    ) -> dict[str, Any]:
        """POST /plans + POST /subscriptions — REAL in Razorpay TEST mode,
        live-verified end to end 2026-08-26 (tracking/BUILD_LOG.md's
        "mandate rail pivot" entry; tracking/DECISIONS.md and
        tracking/TRACK_BAR.md section 0, same date). THIS IS NOW THE
        PRIMARY / DEFAULT mandate-registration method for Promise Keeper's
        crown-jewel instrument, replacing create_mandate_registration_link
        (kept in this file, unchanged, as the alternate rail — see that
        method's docstring). It is the only Razorpay surface on this
        account where the full lifecycle was proven genuinely real, human-
        verified, not just registration:
          Plan `plan_TULfhYrG9rmMjR` -> Subscription `sub_TULfqScOEmQ57p`
          (total_count=1) -> authorized via netbanking eMandate, captured
          payment `pay_TULmn2CWCOuWDu`, real recurring token
          `token_TULmXon2Xf7bco` -> real token revoke, confirmed gone.
          Future-dated scheduling verified separately (`sub_TUM5ilVyr8rpZZ`,
          `start_at` 7 days out).

        Two-step build, mirroring exactly how it was verified live:
        1. create_plan(amount_inr, description) -> plan_id. `period:
           "monthly"` is REQUIRED by Razorpay's plan schema even though we
           only ever bill once — it is NOT a real recurrence. `total_count:
           1` below is what makes this a one-time debit, not the period
           field. Say this out loud so nobody "fixes" it later.
        2. POST /subscriptions with `total_count: 1` (exactly one billing
           cycle, ever — this IS the one-time-mandate semantics) and
           `start_at` (unix seconds, derived from `debit_date`
           "YYYY-MM-DD" at midnight UTC — same conversion pattern
           create_invoice already uses for `due_date` -> `expire_by`).
           `start_at` is what schedules the debit for the invoice due date
           instead of charging immediately — REQUIRED for this to be a
           "scheduled mandate," not an instant charge.

        `customer` (name/contact/email) is NOT sent to Razorpay as a
        structured field here — the live-verified POST /subscriptions body
        has no customer block (the debtor supplies their own details when
        they open the returned `short_url`, same UX as a registration
        link). Judgment call, documented rather than silently made: it is
        folded into `notes` (flat string key/value, matching Razorpay's own
        notes convention) purely so which debtor this mandate is FOR is
        visible on the Razorpay dashboard and in our own audit trail —
        never sent anywhere that would make it a business amount or an
        auth field.

        AMOUNT LAW: `amount_inr` is caller-supplied only (a ledger record),
        the same law every method in this file follows. The only
        arithmetic is the existing INR -> paise conversion (inside
        create_plan) plus this method's one new conversion, `debit_date` ->
        `start_at` — a CALENDAR conversion, not a money computation.

        Execution is NOT triggered here and has no API call in this flow:
        once the debtor authorizes at `short_url`, Razorpay's own billing
        engine auto-charges at `charge_at` with no manual trigger from our
        side. This is a real, deliberate difference from the old
        UPI/eMandate-via-auth_links flow, which needed a manual recurring-
        charge call. See check_mandate_execution to observe whether the
        auto-charge happened, and revoke_mandate_token to cancel a live
        token.

        Returns {"plan": <raw POST /plans response>, "subscription": <raw
        POST /subscriptions response>} — both untouched, so the
        subscription's real `id`, `short_url`, `charge_at` and `status`
        (starts "created") are directly visible to the caller alongside the
        plan's `id`.
        """
        plan = self.create_plan(amount_inr, description)
        plan_id = plan.get("id")

        # debit_date ("YYYY-MM-DD") -> start_at (unix seconds, midnight
        # UTC). Same conversion pattern as create_invoice's due_date ->
        # expire_by. This is a CALENDAR conversion, not a money computation
        # — the only amount arithmetic in this method happens inside
        # create_plan (INR -> paise), called above.
        start_at = int(
            dt.datetime.strptime(debit_date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
        )

        notes = {
            "customer_name": customer.get("name", ""),
            "customer_contact": customer.get("contact", ""),
            "customer_email": customer.get("email", ""),
            "description": description,
        }

        payload = {
            "plan_id": plan_id,
            "total_count": 1,  # exactly one billing cycle, ever -- the one-time-mandate semantics
            "quantity": 1,
            "customer_notify": 1,
            "start_at": start_at,
            "notes": notes,
        }
        subscription = self._post("/subscriptions", payload)
        return {"plan": plan, "subscription": subscription}

    def check_mandate_execution(self, subscription_id: str) -> dict[str, Any]:
        """A QUERY, not a command — REAL in Razorpay TEST mode. Razorpay's
        Subscriptions billing engine auto-charges the debtor at `charge_at`
        once they authorize; there is no execute API call in this flow
        (the deliberate, documented difference from the old
        UPI/auth_links flow's manual recurring-charge call — see
        create_mandate_via_subscription's docstring). This method OBSERVES
        whether that auto-charge happened; it never fabricates a capture.

        KNOWN QUIRK (tracking/BUILD_LOG.md, 2026-08-26): `subscription.status`
        lags behind the real payment record — it stayed `"created"` even
        after a real capture in the live verification session. Do NOT trust
        the subscription's own `status` field to mean "charged". This
        method therefore never even reads `subscription.status`; instead it
        calls GET /invoices?subscription_id=<id>, which surfaces
        `payment_id` once Razorpay has raised and paid an invoice for a
        billing cycle, then confirms that payment's real status via
        GET /payments/{payment_id}.

        Returns {"executed": bool, "payment": <real payment dict or None>,
        "checked_via": "invoice_lookup"}. `"executed"` is only ever True
        when a real payment object with `status == "captured"` was found —
        no other signal (subscription status, invoice status alone, an
        invoice merely existing, etc.) is trusted enough to report a
        capture that may not have happened.
        """
        invoices = self._get("/invoices", params={"subscription_id": subscription_id})
        for invoice in invoices.get("items", []):
            payment_id = invoice.get("payment_id")
            if not payment_id:
                continue
            payment = self._get(f"/payments/{payment_id}")
            if payment.get("status") == "captured":
                return {"executed": True, "payment": payment, "checked_via": "invoice_lookup"}
        return {"executed": False, "payment": None, "checked_via": "invoice_lookup"}

    def revoke_mandate_token(self, customer_id: str, token_id: str) -> dict[str, Any]:
        """DELETE /customers/{customer_id}/tokens/{token_id} — REAL in
        Razorpay TEST mode, live-verified 2026-08-26
        (`token_TULmXon2Xf7bco` deleted for real; a re-lookup afterwards
        returned 400 "the id provided does not exist" — genuinely gone,
        not just marked cancelled).

        `token_id` only exists after a customer has authorized a
        subscription registration — it appears on the resulting payment
        object's `token_id` field (see check_mandate_execution's returned
        `payment` dict) or is listable via
        GET /customers/{customer_id}/tokens. If nothing has been authorized
        yet, there is nothing real to revoke against — the same honest
        limitation this file has always stated for revoke, just for a
        different reason now (nothing exists yet to revoke, not "the rail
        doesn't work").

        Returns the raw Razorpay response, e.g. {"deleted": true} on
        success. A revoke against an unknown/already-deleted token raises
        RazorpayError (a real 400), exactly like every other real call in
        this file — never silently swallowed.
        """
        return self._delete(f"/customers/{customer_id}/tokens/{token_id}")

    # -- SIMULATED: mandate lifecycle events the sandbox can't complete headless --

    def execute_mandate(self, token_id: str, amount_inr: int, customer_id: str) -> dict[str, Any]:
        """SIMULATED — the DEFAULT path the automated pipeline
        (engine/integration/runner.py) uses. Makes NO network call.

        Why (updated 2026-08-26, packet P12 — mandate rail pivot): NOT
        because the real rail is unproven anymore. See
        create_mandate_via_subscription / check_mandate_execution /
        revoke_mandate_token above and tracking/BUILD_LOG.md's "mandate
        rail pivot" entry for a fully real, human-verified create ->
        authorize -> execute -> revoke lifecycle on the Subscriptions rail
        (plan_TULfhYrG9rmMjR / sub_TULfqScOEmQ57p -> pay_TULmn2CWCOuWDu
        captured -> token_TULmXon2Xf7bco revoked). This method stays
        simulated because Razorpay's real billing engine auto-charges on a
        real `charge_at` and cannot be sped up, while this automated
        pipeline's simulator advances 45 virtual days in seconds — the two
        cannot run against each other without the pipeline blocking on real
        calendar time. That is a SCHEDULING constraint, not a capability
        gap; wiring the real rail into the runner's automated loop is out
        of scope for this packet (tracking/DECISIONS.md, same date).

        To run ONE execution for real instead (a live demo, not the
        automated pipeline): call create_mandate_via_subscription, have a
        human authorize the returned `short_url` via netbanking eMandate,
        then poll check_mandate_execution(subscription_id) — it will
        report the real captured payment once Razorpay's billing engine
        charges it at `charge_at`. No manual recurring-charge call is
        needed or possible in this flow; that is itself the pivot's point.

        Returns a labeled simulated capture payload. `amount_inr` is
        converted to paise (the module's one permitted transform) purely for
        payload shape parity with a real payment object — it is never sent
        anywhere.
        """
        return {
            "simulated": True,
            "reason": (
                "Razorpay TEST-mode recurring-charge execution requires a token "
                "a human already authorized via a real browser/UPI-app "
                "registration flow; this build has no headless way to complete "
                "that step, so execution is simulated (BUILD.md Day 6)."
            ),
            "id": f"sim_pay_{token_id}",
            "entity": "payment",
            "status": "captured",
            "amount": _to_paise(amount_inr),
            "currency": "INR",
            "token_id": token_id,
            "customer_id": customer_id,
        }

    def revoke_mandate(self, token_id: str) -> dict[str, Any]:
        """SIMULATED — the DEFAULT path the automated pipeline
        (engine/integration/runner.py) uses. Makes NO network call.

        Why (updated 2026-08-26, packet P12 — mandate rail pivot): same
        scheduling constraint as execute_mandate, not a capability gap —
        see that method's docstring. The real DELETE is proven
        (`token_TULmXon2Xf7bco` revoked for real, confirmed gone) and lives
        in revoke_mandate_token above, used when a token genuinely exists
        to revoke against; this stub stays simulated so the automated
        45-virtual-day pipeline never has to wait on a real token that only
        exists once a human has authorized a real Subscription.
        """
        return {
            "simulated": True,
            "reason": (
                "No real, script-authorized token exists to revoke in this "
                "build (see execute_mandate's docstring) - simulated (BUILD.md Day 6)."
            ),
            "id": token_id,
            "entity": "token",
            "status": "cancelled",
        }


# -- module-level convenience functions ---------------------------------
# Delegate to a lazily-constructed default client so `import
# engine.action.razorpay_client` never requires keys - only calling one of
# these does. Names match the pre-Phase-C stub interface (create_payment_link
# / create_invoice / execute_mandate / revoke_mandate) plus the REAL
# operations added since (create_customer / create_mandate_registration_link,
# and packet P12's create_plan / create_mandate_via_subscription /
# check_mandate_execution / revoke_mandate_token) so any existing import of
# this module keeps working.

_default_client: RazorpayClient | None = None


def _get_default_client() -> RazorpayClient:
    global _default_client
    if _default_client is None:
        _default_client = RazorpayClient()
    return _default_client


def create_payment_link(amount_inr: int, description: str, customer: dict[str, str]) -> dict[str, Any]:
    return _get_default_client().create_payment_link(amount_inr, description, customer)


def create_invoice(amount_inr: int, description: str, customer: dict[str, str], due_date: str) -> dict[str, Any]:
    return _get_default_client().create_invoice(amount_inr, description, customer, due_date)


def create_customer(name: str, contact: str, email: str) -> dict[str, Any]:
    return _get_default_client().create_customer(name, contact, email)


def create_mandate_registration_link(
    max_amount_inr: int, description: str, customer: dict[str, str], method: str = "upi",
) -> dict[str, Any]:
    return _get_default_client().create_mandate_registration_link(max_amount_inr, description, customer, method)


def create_plan(amount_inr: int, description: str) -> dict[str, Any]:
    return _get_default_client().create_plan(amount_inr, description)


def create_mandate_via_subscription(
    amount_inr: int, description: str, customer: dict[str, str], debit_date: str,
) -> dict[str, Any]:
    return _get_default_client().create_mandate_via_subscription(amount_inr, description, customer, debit_date)


def check_mandate_execution(subscription_id: str) -> dict[str, Any]:
    return _get_default_client().check_mandate_execution(subscription_id)


def revoke_mandate_token(customer_id: str, token_id: str) -> dict[str, Any]:
    return _get_default_client().revoke_mandate_token(customer_id, token_id)


def execute_mandate(token_id: str, amount_inr: int, customer_id: str) -> dict[str, Any]:
    return _get_default_client().execute_mandate(token_id, amount_inr, customer_id)


def revoke_mandate(token_id: str) -> dict[str, Any]:
    return _get_default_client().revoke_mandate(token_id)
