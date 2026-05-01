---
name: Em-dash rule scoped to external output only
description: Em-dashes are fine in terminal replies to Tim. Block them only in external output (emails to non-Tim recipients, published articles, anything Tim ships under his name).
type: feedback
scope: shared
---

# Em-dash rule scoped to external output

Tim's instruction (2026-05-01): "Stop worrying about em-dashes in the terminal. Only block them in external output (emails to anyone but me, articles you write for me etc)."

**Why:** the response-gate Stop hook was firing repeatedly on terminal replies, which Tim reads directly. The em-dash style preference exists because Tim does not type em-dashes himself, so external output that bears his name should match his typing style. Terminal banter does not bear his name.

**How to apply:**
- Terminal replies to Tim: em-dashes allowed, no enforcement.
- External-bound output (emails to recipients other than Tim, published articles, public PR descriptions, public commit messages, public docs that ship under Tim's name): em-dashes still banned, replace with comma or full stop.
- The Stop hook in `shared/hooks/response_quality_check.py` no longer flags em-dashes.
- Email-sending utilities and article generators must enforce the rule themselves before sending.

**Other rules unchanged:**
- No human time-duration estimates for Claude's own work, still enforced.
- No deferral phrases (logged for later, TODO, follow-up), still enforced.
- CEO-style targeting (concise, decision-oriented, no engineer narration), still enforced.
- Maximum 3 questions / asks in one response (Tim's ADHD constraint, 2026-05-01).

**Where this is enforced:**
- `shared/hooks/response_quality_check.py` (em-dash check removed from `find_violations`)
- `shared/rules/reply-style.md` (canonical reply-style rule)
- Outbound email senders: any send_*.py utility must scrub em-dashes from the body before SMTP send if recipient is not Tim
