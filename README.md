# Promise Keeper

Recovery tools send messages; a message just asks the customer to decide again later, at a colder moment. Promise Keeper converts stated intent into a self-executing payment instrument (a scheduled mandate on Razorpay's rails) and only falls back to messages when there's no commitment to capture.

Two scenes, one engine:
- **Scene 1** (B2B overdue-invoice recovery): triage → promise extraction → trust-weighted escalation → promise-to-mandate conversion
- **Scene 2** (checkout drop-off recovery): cause triage → matching instrument (scheduled mandate / delivery-secured mandate / payment link)

Built for the Razorpay AI Buildathon 2026, Track 3 (AI Revenue Recovery).

## Quick start (2 commands)

```
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File setup.ps1
powershell -ExecutionPolicy Bypass -File run.ps1

# Mac/Linux
./setup.sh
./run.sh
```

`setup` creates a Python venv, installs dependencies, and installs the dashboard's `node_modules`. `run` starts the API and the dashboard together. The app runs fully offline with no API keys — copy `.env.example` to `.env` and add real keys only for the live-Razorpay / real-channel demos.

Run the test suite (577 tests as of this writing, fully offline):
```
.venv/Scripts/python.exe -m pytest tests/   # Windows
.venv/bin/python -m pytest tests/           # Mac/Linux
```

## The honesty split

This project measures what it can and labels what it can't (CLAUDE.md's design law): **Tier 1** figures (extraction accuracy vs. hand labels, triage accuracy, cost per ₹) are genuinely measured against this repo's dataset. **Tier 2** figures (the 3-arm recovery comparison in `eval/run_arms.py` / `metrics.json`) are simulated against frozen, scripted personas and are always labeled as such — never presented as real-world proof. See `tracking/TRACK_BAR.md` for the full breakdown with proof links into the repo.

## AFA segmentation: where the regulator drew the line we were missing

RBI's Digital Payments – E-Mandate Framework, 2026 (notified 21 April 2026) sets a ₹15,000-per-transaction threshold for Additional Factor of Authentication (AFA) on recurring e-mandate debits. This is the segmentation boundary this project's thesis was originally missing — it isn't a design choice Promise Keeper invented, it's a line the regulator already drew:

- **Under ₹15,000:** the mandate executes unassisted. No further customer action is needed once it's registered — this is the "self-executing instrument" case the whole product thesis rests on.
- **Above ₹15,000:** the mandate still schedules, still pre-commits the debtor, and still sends the same pre-debit (T-1) and post-debit notices — but execution requires an additional factor of authentication, i.e. a human-assisted step at debit time. The instrument still converts a stated promise into a scheduled commitment; it just can't complete unattended past this line.

**A genuine scope ambiguity, stated honestly rather than resolved in our favor:** sources disagree on whether this ₹15,000 AFA line extends to NACH / netbanking eMandate — the primary rail this project's own Razorpay sandbox testing verified as fully real (`tracking/TRACK_BAR.md` §0). Law-firm summaries of the framework scope its text explicitly to "cards, PPIs, and UPI" and do not name NACH:

> "RBI's Digital Payments – E-mandate Framework, 2026... appl[ies] to all payment system providers and participants handling recurring transactions through cards, UPI, and PPIs"
> — [SCC Times, "RBI notifies Digital Payments — E-mandate Framework, 2026"](https://www.scconline.com/blog/post/2026/04/24/rbi-issues-digital-payments-e-mandate-framework-2026/)

At least one consumer-facing publication, by contrast, treats card, e-NACH, and UPI AutoPay as following the same aligned thresholds:

> "All three mandate structures follow aligned guidelines: transactions below ₹15,000 can go through without an additional factor authentication (AFA)..."
> — [PayPro Global, "What is India's RBI e-Mandate?"](https://payproglobal.com/answers/what-is-indias-rbi-e-mandate/)

We could not find primary RBI text resolving this either way as of this writing. **This project designed for the stricter reading** — treating netbanking eMandate as if it were AFA-gated above ₹15,000 the same way cards/UPI/PPIs explicitly are — rather than assuming the ambiguity in our own favor. The pre-debit and post-debit notice requirements (also part of this same framework) are built for every mandate regardless of amount; see `tracking/PROBLEM_TASTE.md` (sourced claims) and `tracking/TRACK_BAR.md` §2 (compliant escalation) for the implementation.

## More detail

- `promise-keeper-v3-final-master-doc.md` — the full spec (what and why)
- `BUILD.md` — day-by-day build plan and acceptance criteria
- `CLAUDE.md` — design laws and tracking discipline this repo was built under
- `tracking/` — the honesty paper trail: what broke (`BUILD_LOG.md`), why this problem (`PROBLEM_TASTE.md`), the track's own bar (`TRACK_BAR.md`), where AI is/isn't used (`AI_JUDGMENT.md`), build health (`BUILD_QUALITY.md`), and every deviation from spec (`DECISIONS.md`)
