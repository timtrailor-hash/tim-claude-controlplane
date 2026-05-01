---
name: Don't defer fixes — do them now
description: Tim explicitly rejected "logged for later" / "TODO" / "queued for next session" responses. Filing things keeps forgetting them. Fix now or escalate now.
type: feedback
scope: shared
---
# Don't defer fixes

**Hard rule (2026-04-30, after Pattern 28 hook trap was deferred and Tim called it out):** if a fix is identified during a session, **do it that session**. Don't write "logged for later", "TODO when there's bandwidth", "tracked in lessons.md", "follow-up slice", "out of scope for this PR". Those phrases are the failure mode — they look like rigour but in practice the fix never happens because nothing surfaces it again until the same trap hits Tim.

**Why:** Tim is the CEO; the engineer is Claude. Filing things creates a TODO backlog that has no owner — Tim won't read it, future-Claude won't see it unless the right topic file gets loaded. The real-world outcome is the bug stays.

**How to apply:**
1. **Default to fixing now**, even if the fix needs subagent review. Don't pre-classify as "out of scope" — let the reviewer decide.
2. The only acceptable defers are when **fixing now would create a worse risk** than the bug being fixed (e.g. a security boundary that needs a proper test suite first). When deferring for that reason, say so explicitly: "fix-now is unsafe because X, the safe fix needs Y, and Y requires Z step before it can ship." Then plan Z immediately.
3. **Never** defer with words like "for later", "when there's bandwidth", "follow-up", "next session". Those are velocity-theatre and they mean "this won't happen".
4. If a fix is identified mid-session and the session already has a primary goal, **fix it in parallel** (subagent + parallel tool calls) rather than queueing.
5. If genuinely too large to fix in one go, **break it into a thin slice that ships now** and document the remaining slices as concrete next-session tasks tied to a specific trigger (not "when there's time").

**Counter-example (the bad version, captured during a personal-side session):** Claude identified the Pattern 28 hook trap, the reviewer found bypasses in the proposed regex fix, and Claude said "logged for later, paraphrase as workaround". Tim called it out: "Stop filing things for later. They then keep getting forgotten. What is the proper fix - do that now." The proper fix (bashlex AST walk) was 60 lines of code and a 12-case test suite. It shipped in the same session.

**Test (apply when drafting any response that contains a deferral):** if Claude is writing "log this", "TODO", "follow-up", "later", "when there's bandwidth", pause. Either fix it now, or explain why the fix requires a specific blocker that cannot be removed this session.
