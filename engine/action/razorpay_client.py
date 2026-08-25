"""Razorpay TEST-mode client — REAL for the sandbox-verified endpoints,
SIMULATED (clearly labeled) for the two mandate-lifecycle calls this offline
build cannot complete end to end.

Sandbox verification (CLAUDE.md Day-1 priority #1, `scripts/verify_razorpay_sandbox.py`
-> `tracking/razorpay_sandbox_report.json`, 8/8 probes green, `tracking/TRACK_BAR.md`
section 0, verified 2026-08-26) found REAL in TEST mode:
  - POST /payment_links          -> create_payment_link
  - POST /invoices               -> create_invoice
  - POST /customers               -> create_customer
  - POST /subscription_registration/auth_links -> create_mandate_registration_link
    (the crown-jewel mandate rail — works for BOTH method="upi" and
    method="emandate", each returning a real, hosted registration short_url)

NOT REAL here (execute_mandate / revoke_mandate always return a payload with
a literal `"simulated": True` field + a `reason` — see each method's
docstring for exactly why and the one manual step that would make one of
them real for a demo):
  - execute_mandate  — a real recurring charge needs a token a human already
    authorized via a registration link's short_url in an actual browser/UPI
    app; this build has no headless way to complete that human step.
  - revoke_mandate   — same blocker: nothing script-authorized exists to
    revoke against.

KNOWN GOTCHA (tracking/BUILD_LOG.md, 2026-08-26): `POST /v1/orders` accepts a
`token: {...}` block with HTTP 200 but silently drops it (fetched back as
`token: null`). Do NOT build mandates from hand-rolled token orders —
`subscription_registration/auth_links` is the only productized mandate path
this client uses, exactly as the verified probe called it.

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
        mode (verified 2026-08-26; tracking/TRACK_BAR.md section 0). This IS
        the crown-jewel mandate rail: it returns a hosted registration
        `short_url` a human authorizes once in a browser/UPI app, producing
        the token a later real execute_mandate would charge against (see
        that method's docstring for why this build can't script that step).

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

    # -- SIMULATED: mandate lifecycle events the sandbox can't complete headless --

    def execute_mandate(self, token_id: str, amount_inr: int, customer_id: str) -> dict[str, Any]:
        """SIMULATED — not real in this build. Makes NO network call.

        Why: Razorpay's real recurring-charge path (a payment created
        against an authorized token) needs a `token_id` that only exists
        after a HUMAN completes the UPI/eMandate authorization at a
        registration link's `short_url` (see create_mandate_registration_link)
        in an actual browser or UPI app. There is no headless/API way to
        complete that authorization step, so this build cannot produce a
        real token to charge against inside an automated test or script.

        Path to make ONE execution real for the demo (documented, not
        automated): (1) call create_mandate_registration_link and open the
        returned short_url in a real browser; (2) manually complete the
        UPI/netbanking authorization as a human; (3) read the resulting
        authorized token_id back from the Razorpay dashboard or webhook;
        (4) use that real token_id in a real recurring-charge call. That
        manual step is why this function stays labeled simulated instead of
        pretending to automate it.

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
        """SIMULATED — not real in this build. Makes NO network call.

        Same blocker as execute_mandate: revoking a token (Razorpay's token
        delete endpoint) needs a token_id that only exists after a human has
        authorized a registration link; without a manually-authorized token
        there is nothing real to revoke. Same one-manual-step path as
        execute_mandate's docstring would make this real for a demo.
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
# / create_invoice / execute_mandate / revoke_mandate) plus the two new REAL
# operations (create_customer / create_mandate_registration_link) so any
# existing import of this module keeps working.

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


def execute_mandate(token_id: str, amount_inr: int, customer_id: str) -> dict[str, Any]:
    return _get_default_client().execute_mandate(token_id, amount_inr, customer_id)


def revoke_mandate(token_id: str) -> dict[str, Any]:
    return _get_default_client().revoke_mandate(token_id)
