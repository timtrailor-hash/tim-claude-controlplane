---
name: Response-gate hook — enforce response style technically, not via memory
description: 2026-04-30 — Tim approved a Stop-hook that scans Claude's response text against the response-style rules and forces a re-do on violation. Memory alone isn't holding.
type: feedback
scope: shared
---
# Response-gate hook

**Why this exists (2026-04-30):** Tim has multiple feedback files about response style: `feedback_response_structure.md` (short, separate done from decisions, ≤3 asks), `feedback_plain_english.md` (no jargon), `feedback_no_time_estimates.md` (don't say "this'll take 30 min", Claude isn't a human, time estimates don't apply). Claude keeps violating them. Memory loads at session start but doesn't gate output. Tim asked: "How do we harden your communication rules - you keep doing this. Do we need a hook checking a response before it's displayed?"

**Mechanism:** Claude Code Stop hook fires when the assistant turn ends. With `exit 2 + stderr message`, the hook BLOCKS the stop and feeds the message back as a continuation. Claude self-corrects on the next pass before Tim sees the bad response. This is the closest thing to a true pre-display gate that Claude Code's hook system supports today.

**Hook location:** `shared/hooks/response_quality_check.py` in the controlplane (deployed to `~/.claude/hooks/` as the Stop hook on each machine).

**Rules enforced (technical, not advisory):**
1. **Em-dashes scoped to external output only** (2026-05-01). Terminal banter exempt; emails to non-Tim recipients and published articles still banned. Hook no longer flags em-dashes; email senders enforce the rule themselves.
2. **No human time estimates.** Phrases like "30 minutes", "a few hours", "by tonight (in the duration sense)", "in a day or two", "this week" applied to Claude's own work. Claude is an LLM; clock time doesn't apply to its work the way it does to a human. Sequencing words ("next", "after", "before this ships", "in the same session") are fine.
3. **Length cap on default responses:** soft cap at ~150 words for normal turns, hard cap at ~400 unless the response is a structured deliverable Tim asked for.
4. **Max 3 asks per response** (Tim's ADHD constraint, 2026-05-01). See `feedback_response_structure.md`.
5. **Plain English** — no engineering jargon when Tim's audience is Tim-as-CEO. The hook can't fully judge this but it can flag known jargon.

**False-positive direction:** if the regex flags something benign, the hook should warn-and-allow rather than block-forever. Implementation includes a max-retry counter so a too-strict rule can't loop Claude. After N retries on the same violation, the hook logs and lets the response through — Tim sees both the message and the violation flag. That's a feature, not a bug: a stuck loop signals the rule needs tuning.

**Memory + hook hierarchy:**
- Memory files describe the rule (this file + the existing feedback_response_*.md set).
- Hook enforces a subset of the rule that's mechanically detectable (em-dashes, regex time-units, length).
- Subagent reviewers (code-reviewer, gemini, chatgpt) catch the harder cases — jargon, structure — when they are invoked.
- The hook is the LAST line of defence, not the only one.

**Status:** initial implementation 2026-04-30 with the three mechanical checks (em-dashes, time-duration regex, length cap). Tuning will happen as Tim sees false positives or false negatives in the wild.
