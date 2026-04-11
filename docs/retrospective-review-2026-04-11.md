# Retrospective Review — 20-Commit Platform Rebuild (2026-04-11)

## Context
The tim-claude-controlplane repo was built in a single 3-hour session on 2026-04-11
(00:51–03:27 BST). A 24-hour audit at 10:00 BST identified that 19 of 20 commits
had no per-commit /review or /debate artifact. Tim reviewed the audit findings and
explicitly approved this retrospective review as one of 6 remediation fixes.

## Commits Reviewed (19 post-initial-commit)
```
06a8aa9 Phase 1: versioned control-plane with deploy + verify
cc038e3 Phase 2: executable safety scenario suite (30 tests) + fix 3 broken hooks
7ee4b90 Phase 4: rationalize LaunchAgents (16 → 13)
9f11ffd Phase 5: transcript-canonical memory architecture
73fbc15 Phase 6: degraded mode matrix + machine-readable policies
d9a5a3c Phase 7: machine-readable policies + choke-point wrappers
3e4ffe8 Post-audit fixes: behavioral tests + deploy.sh syntax + drift_check symlinks
c6f5259 Remove broken system-monitor plist
8ca43b6 Post-rebuild fixes: services.yaml alignment + drift_check for memory repo
4bce556 Scope drift_check to canonical memory repo + relocate deploy backups
e0c3509 drift_check: remote SSH find needs -L to follow skill dir symlinks
b29cb65 Executable authority map: system_map.yaml + shared/lib parser
425331d Add post-deploy live-acceptance gate (Pattern 20 fix)
9703f42 live_acceptance: honor required_on_deploy flag; ttyd expects 401
038f10a system_map.yaml: fix 3 probe misconfigurations caught by first real run
8cd7176 Batch: rename_guard + commit_guard + system_inventory + /debate gates
f4e287f system_inventory: only flag .git dirs without .git/config as stray
7176eb1 Add system_map.yaml schema validator + verify.sh gate
00dd06a Flip commit_guard + live_acceptance to strict; add inventory reconciliation
```

Aggregate: 98 files changed, 9658 insertions.

## Reviewer 1: Gemini 2.5 Flash — DISAGREE (changes requested)

Top 3 concerns:
1. **No rollback after failed acceptance.** live_acceptance.sh failure leaves all
   changes applied. The word "gate" implies blocking or reverting; neither happens.
2. **Tests hit live symlinks, not repo copies.** Behavioral tests validate
   ~/.claude/hooks (live), not the repo's shared/hooks. A stale symlink means
   tests silently validate the previous version.
3. **printer_safety_check.sh has zero test coverage.** This is the highest-stakes
   hook in the system (printer allowlist) and has no behavioral tests.

## Reviewer 2: GPT-5.4 Mini — PARTIALLY AGREE (changes requested)

Top 3 concerns:
1. **No rollback mechanism.** System left in inconsistent state after a failed
   post-deploy acceptance check.
2. **Drift and parse failures are warnings, not blockers.** system_inventory.sh
   drift and SSH cross-machine alignment failure both downgrade to warnings.
3. **Tests tied to live symlinks.** Trust in the test suite is weakened because a
   broken deploy could cause tests to silently validate the old version.

## Convergent Findings (both reviewers)
- No rollback mechanism in deploy.sh (top structural defect)
- Tests target live hooks, not repo code
- printer_safety_check.sh untested
- system_inventory drift is WARN-only with no escalation timeline

## Divergent Findings
- **Gemini only:** LaunchAgents copied but not reloaded via launchctl; crontab
  replaced wholesale (could silently delete manual entries)
- **GPT only:** Hostname pattern `*mini*` is too loose for machine detection

## Net Verdict: CHANGES REQUESTED
The architecture is sound in intent. Implementation has a structural gap (no
rollback, weak gates) and a critical test coverage gap (printer safety hook
untested). These should be addressed before treating the deploy pipeline as
production-reliable.

## Recommended Action Items (priority order)
1. **CRITICAL** — Add rollback path to deploy.sh (snapshot before, restore on failure)
2. **HIGH** — Add printer_safety_check.sh behavioral test
3. **HIGH** — Fix LaunchAgent activation (launchctl bootout/bootstrap after copy)
4. **MEDIUM** — Run full pytest during deploy (not --quick)
5. **MEDIUM** — Harden SSH alignment check (FAIL, not WARN)
6. **LOW** — Tighten hostname detection (explicit allowlist)
7. **LOW** — Track system_inventory strict-mode escalation deadline

## Authorization
Tim reviewed the 24-hour audit findings and explicitly approved all 6 remediation
fixes (including this retrospective review) on 2026-04-11.
