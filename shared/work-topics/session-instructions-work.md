---
name: Session start checklist (work-laptop side)
description: What to do at the top of every work-side Claude session. Loaded automatically via MEMORY.md.
type: project
scope: shared
---

# Session start checklist (work-laptop side)

Run these at the top of every session before doing any task work.

## 1. Load the rules and the user profile

The `~/.claude/rules/*.md` files load automatically. Sanity-check by reading `user_profile-work.md` in this folder so reply style and CEO/ADHD constraints are fresh.

## 2. Confirm the bridge MCP is loaded

Check that the three bridge tools are available:
- `bridge_push_notification(title, body, level)`
- `bridge_send_email_self(subject, body)`
- `bridge_search_personal_memory(query, scope, limit)`

If any are missing, the bridge MCP did not register. Run the work-side setup script (`tools/work_setup.sh` in the local checkout of the `claude-bridge` repo) and quit + relaunch Claude Code.

## 3. Confirm the work memory MCP is loaded

Check that `mcp__memory_work__search_memory` is available. Same fix as above if missing.

## 4. Confirm the slash skills are visible

Expect: `/chatgpt`, `/gemini`, `/debate`, `/review`. The shared skills are materialised from the controlplane's `shared/skills/` (allowlisted entries only). If a skill is missing, re-run the materialiser inside `work_setup.sh` and relaunch.

## 5. Read open project state

If there is a topic file relevant to the current task, read it before starting. The topic-files index lives in `MEMORY.md`. The most load-bearing files for a fresh session:
- `lessons.md` (incident patterns and their enforcement)
- `quality-toolchain.md` (review pipeline)
- `memory-system.md` (memory architecture and three-query-shape rule)
- `work-bridge-phase2-plan.md` (current phase-2 status, what each side owns)
- For the full integration plan: query `bridge_search_personal_memory("work-claude-integration plan", scope="shared")`. The full plan is scope: shared on the personal side; the work side fetches it on demand rather than copying it locally.

## 6. If Tim left an instruction

Look for a freshly-written topic or a scope:work email landed via the bridge. The bridge poller writes inbound work-bound responses into the work-side memory; check if anything new appeared since the last session.

## 7. Default to action

Do not ask Tim what to do if a topic file already says what to do. Proceed and report. The "Before Saying I Can't" checklist in `shared/rules/operational.md` (work-safe sections) is the safety valve against learned helplessness.

## 8. Plan-mode triggers (work side)

Enter plan mode and confirm before implementing for:
- Any change that touches corporate code paths the team relies on.
- Any new infrastructure (services, scheduled tasks, monitoring) on the work laptop.
- Any refactor touching three or more files.
- Anything that crosses the work / personal boundary (pushes through the bridge, syncs memory, modifies the bridge keys).

Skip plan mode for: reading or researching, simple edits, memory-only writes, single-file fixes.

## 9. Status sync back to personal side

When a slice ships on the work side, send a short status email to Tim's address via `bridge_send_email_self`. Subject prefixes the slice (for example, `WORK-SLICE-K-DONE` or `WORK-DIVERGENCE`). Body lists what shipped and what is still open. The personal side reads these and updates the canonical phase-2 plan.

## 10. End-of-session

The SessionEnd hook handles transcript indexing, drift-check, and auto-review automatically once slice E ships. Until then, no manual close-out is needed.
