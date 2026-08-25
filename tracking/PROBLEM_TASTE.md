# PROBLEM_TASTE.md — "did you pick something that actually matters"

Every claim we make about why this problem matters, with its source. When a claim enters the README or video script, it must exist here first (CLAUDE.md §4). Nothing here is independently re-verified yet — sources below are what the master doc asserts; if a firmer primary citation is found before README time, update the Source column.

| Claim | Where used | Source (as given) | Verified? |
|---|---|---|---|
| ₹8.1 lakh crore locked in MSME receivables | Problem framing, video 0:00 | master doc §1.4 (judging criteria table) | ⬜ not independently re-verified — needs a primary citation before README |
| ~70% cart abandonment rate | Problem framing, video 0:00 | master doc §1.4 | ⬜ not independently re-verified — this is an industry-average figure, needs a source before README |
| Razorpay named both directions (B2B receivables recovery + checkout drop-off recovery) in the Track 3 brief | Justifies the two-scene structure | master doc §1.2/§1.4 — track brief itself (external, not in repo) | ⬜ confirm against actual buildathon brief text |
| Razorpay's WhatsApp Payments integration supports one-time payments only, no mandate/commitment setup in-chat | Master doc §8.5 "the gap we fill" — Scene 2 USP | master doc §8.5, attributed to "their own FAQ" | ⬜ needs the actual Razorpay FAQ/docs URL before this claim goes in README or video |
| One-Time Mandate (OTM) rail exists and supports create/register/execute/revoke lifecycle in TEST mode | Crown-jewel feasibility for delivery-secured + scheduled mandates | CLAUDE.md §5 Day-1 priority #1 — UNVERIFIED, this is the single biggest risk in the whole build | ⬜ BLOCKING — cannot verify without Razorpay TEST keys (user is obtaining them) |
| Razorpay Magic Checkout emits an abandoned-cart webhook with a defined schema | Scene 2 trigger, master doc §3.3 | master doc §3.3 | ⬜ not independently re-verified |

## Notes
- Two rows above are marked BLOCKING/needs-primary-source specifically because they gate honest claims in the README's "what's real" table (master doc §4.5). Do not let any of these slide into the video script unmarked.
- The OTM sandbox-capability row is the one CLAUDE.md flags as highest priority — resolve it the moment Razorpay TEST keys are available, before any further Razorpay wiring work.
