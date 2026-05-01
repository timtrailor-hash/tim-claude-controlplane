---
name: Work-bridge phase-2 plan (shared between personal and work Claude)
description: Single source of truth for the phase-2 split between personal-side Claude and work-side Claude. Both sides should agree on this. Work side fetches via bridge_search_personal_memory after activation.
type: project
scope: shared
---
# Work-bridge phase-2 plan (shared)

Phase 1 (steps 1 through 8 of `work-claude-integration.md`) shipped the
wire and one approval surface. Phase 2 builds the actual feature
parity that lets work-Claude do useful cross-environment work.

## Slices and ownership

| Slice | Description | Built by | Activated by |
|---|---|---|---|
| A | Work-side bridge MCP wrapper. Three native tools (push/email/memory) replace the smoke_test.py path. | Personal side (shipped: claude-bridge bc3fe87) | Work side runs `bash ~/code/claude-bridge/tools/work_setup.sh` |
| B | Work-side memory infra: ChromaDB + FTS5 over work-side JSONL. Separate from personal data. | Personal side (shipped: claude-bridge dbf7b91) | Work side same setup script. Restart Claude Code so memory_work MCP server registers. |
| C | Sanitise chatgpt / gemini / debate skills. Replace credentials.py probe with env then Keychain then credentials.py fallback resolver. Move from deny_explicit to allow.skills. | Personal side (shipped: tim-claude-controlplane 3fcf7c9) | Work side reruns the materialiser and rsyncs `~/.claude/materialised/shared/skills/` into `~/.claude/skills/` |
| D | Generate WORK_OPENAI_API_KEY and WORK_GEMINI_API_KEY. Set spending caps. Add to work-laptop Keychain via `security add-generic-password`. | Tim (manual) | Tim |

## What "done" looks like for each side

### Personal side
- [x] Bridge gateway daemon healthy (`/bridge-health` returns ok).
- [x] Auto-approver running for `search_personal_memory` queries during the inventory window.
- [x] Phase-2 slices A, B, C committed to their respective repos.
- [x] Activation email sent to Tim's work address.
- [ ] Wait for work-Claude's confirmation of activation success and plan agreement.

### Work side
- [ ] Pull both repos (`tim-claude-controlplane`, `claude-bridge`).
- [ ] Run `tools/work_setup.sh` (idempotent).
- [ ] Re-run materialiser. Confirm chatgpt / gemini / debate appear in `~/.claude/materialised/shared/skills/`.
- [ ] rsync materialised skills into `~/.claude/skills/`.
- [ ] Quit and relaunch Claude Code so `bridge` and `memory_work` MCP servers register.
- [ ] Verify three new tools: `bridge_push_notification`, `bridge_send_email_self`, `bridge_search_personal_memory`.
- [ ] Verify three new slash skills: `/chatgpt`, `/gemini`, `/debate`.
- [ ] Run `bridge_search_personal_memory("phase-2 plan responsibilities", scope="work", limit=5)` and confirm this topic file is in the response.
- [ ] Send a `bridge_send_email_self` reply to Tim with subject `"PHASE-2-CHECKED"` and body listing what is now active plus any divergence from this plan.

## What is NOT in scope for phase 2

- **screenshot, dream, deep-context, memory, autonomous, second-opinion** skills. They have personal-path content beyond the credentials probe and need targeted slices each. Stay in `deny_explicit`.
- **Skill: secure-delete, printer-related, governors-related, autofaizal pipeline** — personal-only by design.
- **Sync of personal session JSONLs to work side** — explicitly NOT in scope. Each side has its own memory.

## Coordination protocol going forward

- Personal side writes the canonical plan in this file. Updates go through `/review` like any other change.
- Work side reads the plan via `bridge_search_personal_memory`. Auto-approve covers memory queries during active development windows; otherwise Tim taps Approve.
- Status sync: work side emails personal side via `bridge_send_email_self` with structured subjects (`PHASE-2-CHECKED`, `WORK-DIVERGENCE`, `BLOCKED-ON-X`). Tim or personal-side Claude reads the email and updates this file.
- Both sides treat this file as the truth. If either side disagrees, the disagreement gets logged here, not silently acted on.

## Status (2026-05-01, end of phase-2 build window)

- Slices A, B, C built and shipped on personal side.
- Slice D awaiting Tim.
- Activation email queued for the work laptop.
- This shared plan written. Work-Claude will fetch and confirm.
