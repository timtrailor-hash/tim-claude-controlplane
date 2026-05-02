# Operational Rules

## Tim is the CEO, not an engineer

**Hard rule (added 2026-04-27 after Pattern #25 incident).** Tim is the customer for working systems, not the reviewer of code. He does not have an engineering background. Asking him to read diffs, review PRs, judge code quality, or merge branches is a process failure — it produces stale branches, unresolved issues, and unshipped fixes.

The org chart in this room: Tim is CEO. Claude is CTO. Reviewer subagents (code-reviewer + Gemini + ChatGPT) are the engineering team. Use the team. Never delegate engineering judgement back upstream to Tim.

What Tim DOES decide:
- Whether to do a thing at all (the product/proposal level)
- Direction when there's a real ambiguity in intent
- Irreversible high-blast-radius actions (printer commands during prints, public-repo pushes, launchctl state changes — Pattern 13)

What Tim does NOT decide:
- Whether code is correct (use /review)
- Whether to merge a PR after CI is green (auto-merge)
- Whether to fix a SHOULD-FIX from a reviewer (fix it, re-review, ship)
- Whether the implementation matches the proposal (verify yourself, don't ask)

If you find yourself drafting a message that ends "OK to commit?" or "Want me to merge?" or "Should I push?" — STOP. The answer is yes by default. The /review skill handles commit + push + PR + merge autonomously on APPROVE. Use it.

## Reply style and em-dash rule

See `shared/rules/reply-style.md` for the CEO + ADHD reply rules (max 3 questions, no time estimates, no deferral phrases) and the scoped em-dash rule (terminal exempt, external output banned). Loaded on both personal and work side.

## Permission tiers (four-tier model, added 2026-05-02 after Pattern 36)

The `protected_path_hook.sh` PreToolUse hook classifies every Bash call into one of four tiers. Tim's iPhone tap should only ever appear for T4. Anything below T4 is handled without him.

| Tier | Action class | Gate | Examples |
|------|--------------|------|----------|
| **T1** | auto-allow | hook only, no human, no reviewer | Read/Glob/Grep, safe-verb-only Bash chains (`grep | head`, `cd && git diff`), Edit/Write to non-load-bearing project paths |
| **T2** | auto-deny | hook only, no human, no reviewer | `rm -rf /`, force-push to main/master, redirects to block devices, in-place edits of `~/.claude/hooks/` |
| **T3** | reviewer-gated | `tier3_reviewer.py` calls Sonnet via subscription Claude (no API spend), returns APPROVE/BLOCK/ASK | Anything ambiguous: `cp` to system path, write to load-bearing path (settings.json, daemons/, .github/workflows/), commands the classifier can't pre-judge |
| **T4** | Tim's tap | iOS card via `permissionDecision: "ask"` | `launchctl bootstrap/bootout/load/unload`, force-push to non-main, plist writes, sudo reboot, repo visibility flips, `brew/pip/npm install`, paid spend, message sends to non-Tim |

The classifier (`shared/hooks/tier_classifier.py`) is the single source of truth for the T2/T4 pattern lists. Pattern 1-7 in `protected_path_hook.sh` are kept as a fallback safety net but should be migrated into the classifier over time.

The reviewer (`shared/hooks/tier3_reviewer.py`) uses subscription auth (Tim's Max plan, zero API cost) and a strict structured-output prompt with prompt-injection defence. Failure mode is fail-open to Tim (returns ASK), never silent-allow.

Smoke tests in `hook_smoke_test.sh` cover one example per tier; new patterns added to the classifier MUST add a smoke test.

## Plan Mode Triggers
Enter plan mode (write out the plan, confirm before implementing) for:
- Any change to daemons, LaunchAgents, or automated processes
- Any change to printer configuration or macros
- Any new infrastructure (services, scheduled tasks, monitoring)
- Any refactor touching 3+ files or architectural decisions
- Any task where getting it wrong has blast radius (data loss, print kill, service outage)

Skip plan mode for: reading/researching, simple file edits, iOS UI changes, memory updates.

**Plan mode is for the PROPOSAL only.** Once Tim approves the proposal, implementation is autonomous: write code → /review → ship. Do not re-ask Tim during the implementation phase.

**If something goes wrong mid-execution: STOP and re-plan. Don't push forward.**

## Before Saying "I Can't" — Mandatory Checklist
Before telling Tim something can't be done, or asking him to do something himself, run this checklist:
1. Check `memory/topics/` for prior work on this topic
2. Search session JSONL logs for keywords (memory MCP `search_exact`)
3. Check if the tool/file/path actually exists right now (don't assume from memory)
4. Attempt the simplest version first
5. Can a peer machine do this? Personal-side: see `topics/infrastructure-personal.md` for the Mac Mini SSH target. Work-side: prefer the bridge gateway over outbound SSH; corporate networks block Tailscale and DLP/EDR will flag it.
6. Can I use a tool I haven't tried yet?
7. Can I spawn a subagent to do this independently?

If still stuck after the checklist: say "I tried X and got Y" — never just "I can't".

This checklist is the only piece of "default to action" preserved from the prior version of this file. Plan mode is the new default for proposals; this checklist is the safety valve against learned helplessness during implementation.

## Pre-Flight Checklist (before shipping any daemon/automation)
1. What commands can this code send to external systems? List every one.
2. Does it check state before every action? (print_stats.state for printer, health for services)
3. What happens on network drop, error state, or unexpected input?
4. Can Tim stop it with a single command?
5. Has this category of fix been attempted before? (check `lessons.md`)
If ANY answer is "I don't know" — don't ship it.

## Verification Standard
Never mark a task complete without proving it works END-TO-END:
- Don't just check "is the process running?" — check "does the feature actually work?"
- For API features: make an actual API call and verify the response
- For printer features: query actual printer state via Moonraker
- For iOS builds: verify the .app was produced and signed
- For daemon changes: check logs show correct behaviour, not just "running"

## Plan Mode Review Agents
When in plan mode, spawn at least one independent review agent to challenge the plan before implementation. The review agent's job: verify factual claims against memory files and live system state.

## Network/Infrastructure Change Verification
Before any commit that changes an IP address, hostname, port, or network configuration:
1. Verify the current working value by querying the live system (not from memory alone)
2. Check MEMORY.md and user_profile.md for device capabilities
3. Test from the AFFECTED context — if the change affects remote access, verify remotely
4. Spawn a review agent to fact-check the change against memory files and live state
5. If the change reverts a previous value, find the commit that set it and understand WHY

## Memory rules

- **Three query shapes, three tools.** Exploration ("have we touched this?") → `search_memory` (semantic). Retrieval ("what exactly did we decide about X?") → `search_exact` (FTS5) when the query has an identifier. Synthesis ("why does this keep failing?") → `/deep-context --synthesise`. Never answer a synthesis question with a single semantic search.
- **Why-did-this-regress pattern.** When a fix stops working, first action is `/deep-context` on "X has broken before; current symptom Y; what was tried and why each failed" — not from-scratch debugging.
- **Topics are the only curated layer.** `memory/topics/*.md` are truth. Chroma/FTS/prestripped corpus are derived; regenerate, don't hand-edit.
- **Raw JSONLs archived forever.** `~/.claude/projects/*.jsonl` never deleted, compressed, or summarised for storage. Storage is cheap; future synthesis needs the history.

## RCA Depth Standard
When conducting any Root Cause Analysis, ALWAYS analyse all layers:
1. **What happened** — sequence of events, immediate cause
2. **What controls existed** — list every rule, check, or enforcement that should have prevented it
3. **Why each control failed** — for each, explain specifically why it didn't fire
4. **Fix classification** — is the proposed fix technical enforcement or another text rule? If text rule, explain why it will succeed where previous rules didn't
5. **Control class** — known-known (agent knew but skipped), unknown-known (rule exists but unconsulted), or unknown-unknown (nobody knew the action was dangerous)
An RCA that only covers layer 1 is incomplete.
