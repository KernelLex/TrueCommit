# PROBLEM_TASTE.md — "did you pick something that actually matters"

Every claim we make about why this problem matters, with its source. When a claim enters the README or video script, it must exist here first (CLAUDE.md §4). Nothing here is independently re-verified yet — sources below are what the master doc asserts; if a firmer primary citation is found before README time, update the Source column.

| Claim | Where used | Source (as given) | Verified? |
|---|---|---|---|
| ₹8.1 lakh crore locked in MSME receivables | Problem framing, video 0:00 | master doc §1.4 (judging criteria table) | ⬜ not independently re-verified — needs a primary citation before README |
| ~70% cart abandonment rate | Problem framing, video 0:00 | master doc §1.4 | ⬜ not independently re-verified — this is an industry-average figure, needs a source before README |
| Razorpay named both directions (B2B receivables recovery + checkout drop-off recovery) in the Track 3 brief | Justifies the two-scene structure | master doc §1.2/§1.4 — track brief itself (external, not in repo) | ⬜ confirm against actual buildathon brief text |
| Razorpay's WhatsApp Payments integration supports one-time payments only, no mandate/commitment setup in-chat | Master doc §8.5 "the gap we fill" — Scene 2 USP | master doc §8.5, attributed to "their own FAQ" | ⬜ needs the actual Razorpay FAQ/docs URL before this claim goes in README or video |
| One-Time Mandate (OTM) rail exists and supports create/register/execute/revoke lifecycle in TEST mode | Crown-jewel feasibility for delivery-secured + scheduled mandates | VERIFIED 2026-08-26 via live probes (`scripts/verify_razorpay_sandbox.py`, report: `tracking/razorpay_sandbox_report.json`) | ✅ PARTIAL-REAL: registration links for BOTH UPI Autopay (`frequency: as_presented`) and eMandate issue real `short_url`s in test mode (8/8 probes green). Execute (recurring charge on token) + revoke (token delete) require a human-authorized registration first — one manual browser step in demo prep, else simulated + labeled per BUILD.md Day 6 |
| Razorpay Magic Checkout emits an abandoned-cart webhook with a defined schema | Scene 2 trigger, master doc §3.3 | master doc §3.3 | ⬜ not independently re-verified |

## Notes
- Two rows above are marked BLOCKING/needs-primary-source specifically because they gate honest claims in the README's "what's real" table (master doc §4.5). Do not let any of these slide into the video script unmarked.
- The OTM sandbox-capability row is the one CLAUDE.md flags as highest priority — resolve it the moment Razorpay TEST keys are available, before any further Razorpay wiring work.
