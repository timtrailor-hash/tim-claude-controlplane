---
name: Never give Tim time estimates for work
description: HARD RULE. Claude's time estimates ("half a day", "two hours", "takes N minutes") are meaningless and unhelpful — stop offering them. Describe scope in other terms.
type: feedback
scope: shared
---
**Rule: never quote a time estimate for Claude work to Tim.**

Banned phrasing includes: "takes two hours", "half a day of work", "~30 seconds", "couple of hours", "N minutes", "quick", "overnight", "by end of day", anything calendar- or clock-based about how long a task will take.

**Why:** Tim corrected this 2026-04-22: *"Don't ever give me a time estimate for work. Things like 'half a days work' for Claude are just incorrect and meaningless but you regularly say things like that."* Claude doesn't experience time the way a human engineer does; the numbers Claude produces are fabricated anchors. They either mislead Tim into expecting faster delivery than reality, or signal false precision. Either way they destroy trust.

**How to apply:** When explaining scope, use shape instead of clock:
- "Contained to one file" / "touches the server and the phone app" / "crosses three repos"
- "Small" / "large" / "cross-cutting"
- "Nothing to approve midway" / "needs a device-code paste" / "needs a human review gate"
- Count of affected files or subsystems if useful
- Ordering language like "do A before B" without minutes attached

Never say how long Claude will take, and never project calendar time. Describe risk, blast radius, approvals needed, and the number of moving parts. That's what's actually useful to Tim.

This rule applies to every future conversation, not just this one. Mechanically enforced in `shared/hooks/response_quality_check.py` (TIME_ESTIMATE_RE). Hook is the safety net; the rule is the primary guidance.
