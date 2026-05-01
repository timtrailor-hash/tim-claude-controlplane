---
name: Lessons learned (work side) — repeated error patterns
description: Domain-agnostic incident patterns and prevention rules for the work-laptop side of the Claude integration. Reviewed every session to avoid repeating mistakes.
type: feedback
scope: shared
---
# Lessons Learned (Work Side)

**Purpose**: This file is reviewed at session start. Every pattern here represents a class of mistake that has happened 2+ times across Claude sessions. If a current task matches a pattern, STOP and apply the prevention rule.

This is the work-side subset — sanitised of personal-network specifics, hardware references, and home-only infrastructure paths. Patterns are kept where the underlying lesson is independent of the original setting.

## Severity Tiers
- **Tier 1 (caused damage)**: Patterns 1, 2, 4, 9, 14, 25, 26 — read at session start.
- **Tier 2 (behavioural / process)**: Patterns 3, 11, 15, 17, 18, 19, 21, 24, 27, 28, 31 — read when relevant.

---

## Pattern 1: "Fix creates new problem"
**What happened**: Automated helpers built to be defensive ended up causing the failure they were meant to prevent — watchdog scripts, auto-recovery hooks, retry loops with side-effects. Each was reactive in concept and dangerous in execution because nobody listed the commands the helper could send before shipping it.
**Prevention**: Before creating any automated process that touches an external system, answer:
1. What commands can this code send? Enumerate every one.
2. Does it check live state before EVERY action?
3. What happens if the network drops mid-execution?
4. What happens if the target system is in an error state?
5. Can the operator stop it with a single command?
If ANY answer is "I don't know" — don't ship it.

## Pattern 2: Safety guards added after the incident, not before
**What happened**: State checks, allowlists, and confirmation gates were repeatedly added only AFTER something went wrong. Each was reactive.
**Prevention**: When writing any code that issues commands to external systems (APIs, daemons, schedulers, repos), add the safety guard FIRST — before the happy path. Ask: "What's the worst thing this code could do if called at the wrong time?" That answer drives the guard, not the post-mortem.

## Pattern 3: Silent failures go unnoticed for weeks
**What happened**: An OAuth token expired and went unnoticed for a month. A scheduled backup hadn't run for 7+ days. Three PreToolUse hooks parsed the wrong JSON shape since they were written and silently passed every command for months — discovered only when an integration suite executed them with real payloads.
**Prevention**: Verification must test the USER EXPERIENCE, not just process health. Don't check "is the daemon running?" — check "does the feature actually work?" Make a real API call. Send the hook a real payload and assert deny/allow. Verify the build artefact exists. "Process is running" is never a green light.
**Technical enforcement**: Behavioural integration suite that executes hooks with real Claude Code JSON payloads and asserts decisions.

## Pattern 4: Fixes that don't stick
**What happened**: The same fix was applied 3+ times for the same recurring symptom. Patches kept regressing because each fix patched the caller without removing the underlying capability.
**Prevention**: Before closing any fix:
1. Write down exactly what broke and why.
2. Check memory — has Claude fixed this before? If yes, the fix approach is wrong.
3. Go one level deeper: don't patch the caller, remove the capability. Don't fix the path, eliminate the indirection.
4. If a fix has failed twice, the next fix must include technical enforcement (code guards, config validation, AST-level checks), not another text rule.

## Pattern 9: "Shared token file, multiple writers with different scopes"
**What happened**: Multiple Python files loaded the same OAuth token, refreshed with different scope subsets, and wrote it back. Last writer wins — silently stripping scopes other scripts needed. Re-auths "fixed" it temporarily but the next refresh destroyed the scope set again.
**Prevention**:
1. **ONE writer owns the token file** — a single refresh daemon (a scheduled job, OS timer, or platform-appropriate startup hook) using raw HTTP that preserves all fields.
2. **All other scripts NEVER write the token back**. They may refresh in-memory for the current request but MUST NOT call `creds.to_json()` and persist.
3. **Scopes defined in ONE place** — a single canonical location (an env var, a Keychain entry, or a config module) that every consumer imports. None define locally.
4. **Initial-auth scripts** (OAuth callback handlers, setup CLIs) are the only other legitimate writers. They run once during setup.
**Technical enforcement**: Audit every `open(token, 'w')` site; remove all but the refresh daemon.

## Pattern 11: "Change first, verify after"
**What happened**: A guard condition was modified based on an assumption that was never verified. The change was unnecessary and wrong, but it shipped because the assumption felt reasonable.
**Root cause class**: "Default to action" was misapplied — used as justification to skip verification before making changes. The correct sequence is: gather data → verify → change.
**Prevention**:
1. Before changing anything, verify the assumption driving the change.
2. Read-only checks (file reads, greps, status calls) cost nothing — always do them first.
3. "Default to action" means don't ask Tim for things Claude can check itself. It does NOT mean skip checking.

## Pattern 14: "Path/config changes are atomic transactions, not single edits"
**What happened**: A working directory or canonical path was changed in one place (`shared_utils.py`) without migrating the directory itself, updating the alias, or copying the project file. Cross-machine sync broke for days. The first attempt at a fix just reverted instead of completing the migration.
**Prevention**:
1. Any change to a shared utility module IS a daemon-level change — triggers plan mode.
2. Path changes require: (a) new directory creation, (b) data migration, (c) alias / launcher update, (d) project-file update, (e) service restart, (f) cross-context verification — ALL atomically.
3. Reverting a broken migration is NOT a fix — completing it is.
4. After any cross-context change: verify from EVERY context that consumes it.
**Broader lesson**: Any config change is a transaction, not a command. Done = ALL steps including verification are complete.

## Pattern 15: "Ground self-knowledge in live system state, never memory"
**What happened**: A session generated a system-state document claiming N skills, M hooks, certain directory layouts, etc. — most didn't exist. The doc was treated as authoritative because it sounded plausible.
**Prevention**:
1. Never claim "the system has X" without running the read commands to verify.
2. System documentation MUST be generated from live introspection, not recalled from context.
3. Before stating a capability exists: check the file exists on disk.
4. Any system doc must include the verification commands that were run to produce it.
**Technical enforcement**: Independent reviewer agent (Gemini / ChatGPT) challenges findings against reality.

## Pattern 17: "Observability-layer drift when source of truth changes"
**What happened**: A service registry was updated to remove two entries, plist files deleted — but the health-check script still had the same names hardcoded in its own list. The dashboard flagged both as RED for hours because no test verified the registry and the monitor agreed.
**Prevention**:
1. When a file becomes the source of truth for a list, every consumer must either (a) read from it directly, or (b) be cross-checked against it in a test that fails the build on drift.
2. "Single source of truth" is a test requirement, not an aspiration.
3. Never delete an entry without grepping every consumer.
**Technical enforcement**: Integration test cross-checks the registry against the monitor's hardcoded list; deploy fails on divergence.

## Pattern 18: "Over-eager consolidation drops content"
**What happened**: During a memory-repo merge, a session-instructions file was reduced to a one-line pointer to another file, claiming the removed content "was already in" that target. It wasn't. ~108 lines of real content quietly deleted. Caught only because Tim asked for a re-review.
**Root cause class**: Known-unknown — the rule "don't duplicate across levels" was known, but the target level wasn't actually verified to contain the content before deleting from the source.
**Prevention**:
1. Before removing content claiming "it's in X", grep X to confirm every removed section has a corresponding destination.
2. Merges are not refactoring opportunities. Keep merge commits minimal. Restructure as a separate, reviewable commit.
3. For any content deletion >20 lines, write down what's being removed and where each piece ends up, then diff before/after to verify.
4. Spawn a review agent on any merge that touches load-bearing topic / spec files.

## Pattern 19: "Partial grep before rename"
**What happened**: A canonical path was renamed without grepping every consumer. A second hardcoded occurrence in a downstream monitor was missed; the dashboard surfaced spurious diff failures for 20+ minutes.
**Prevention**: Before ANY rename or delete of a path referenced anywhere:
1. `grep -r "<old-path>"` across every managed location.
2. Also grep settings files, project files, hook scripts.
3. If the old path appears in more than one place — plan the rename as a multi-file atomic change (Pattern 14).
4. Never declare a rename "done" until downstream monitors have been re-run and verified clean.

## Pattern 21: "Consequential impact blindness — planning one system without walking its dependency graph"
**What happened**: A multi-phase rebuild was reviewed by three independent models and got BLOCK verdicts that were then addressed. Yet within hours of shipping, four separate breakages surfaced — each was a dependency of the change that nobody had enumerated during planning.
**Root cause class**: Unknown-known — consumers existed and were known individually, but nobody asked "what consumes this?" for each file being changed.
**Prevention**: In plan mode, before any multi-file change:
1. For every file being modified / deleted / renamed, run `grep -r "<filename-or-path-or-symbol>"` across all managed locations.
2. Build a dependency list: "X is referenced by {A, B, C}. Each needs to be updated atomically or X can't change."
3. The plan isn't "change X" — it's "change X AND update A, B, C".
4. Walk one hop out from the blast radius. Then walk another. Stop only when the next hop has no matches.
5. The plan-mode review agent's FIRST job: challenge the dependency list, not the design.
**Technical enforcement**: A `/blast-radius <file-or-symbol>` skill that returns the full consumer list. Use as a hard gate before any multi-file plan.

## Pattern 24: "Required hook arrays silently emptied during deploy"
**What happened**: A deploy wiped a load-bearing hook stage (`Stop`, `UserPromptSubmit`, etc.) from the harness settings file because the hook script lived only on the live machine and was never migrated into the canonical repo. The deploy correctly snapshotted before wipe but had no way to know the file was load-bearing. Downstream UX feature (status indicator) stayed stuck for days because no hook fired.
**Why controls missed it**: (a) the validator only verified that referenced command paths existed — an entirely empty hook array was silently accepted. (b) Diff tooling compared symlinked dirs but did not compare the harness settings file (intentionally per-machine). (c) No runtime smoke test ever exercised the hook chain end-to-end.
**Prevention**:
1. **Source-of-truth rule**: any custom hook script that lives in the harness hooks directory MUST be added to the canonical repo's `shared/hooks/` in the same commit as the settings change that references it. If it's not in the repo, the next deploy will wipe it.
2. **Static guard**: validator declares per-machine REQUIRED_STAGES and FAILS validation if any required stage is missing or empty.
3. **Runtime smoke test**: deploy verification POSTs a synthetic payload to the hook endpoint and asserts the chain accepts it — not just "the file exists".
**Control class**: Unknown-known — the rule "validate hooks reference real paths" existed but nobody asked "what about empty arrays?"
**Technical enforcement**: REQUIRED_STAGES check in the hook validator + end-to-end hook smoke test, both run on every SessionStart and every deploy.

## Pattern 25: "Asking the CEO to merge the PR"
**What happened**: After implementing a fix, Claude ended responses with "OK to commit?" / "Want me to merge?" / "Should I push?" — pushing engineering judgement back upstream to Tim, who is the customer for working systems, not a code reviewer. Result: stale branches, unshipped fixes, the same proposal raised again next session because the previous one sat unmerged. Recurred multiple times in a single day before being explicitly called out: "I don't have the engineering skills. Set up review agents and work independently. Treat me like the CEO, not a developer. You're the CTO and have the entire engineering team (agents) at your disposal."
**Why controls missed it**: (a) "Default to action" was ambiguous about implementation-phase asks. (b) The /review skill returned a verdict and stopped — the commit/push/merge cycle was implicit, never enforced. (c) Autonomous mode rules were scoped to "Tim is away" rather than "Tim is not the engineer". (d) Generic "ask before LaunchAgent operations" was over-generalised by sessions to "anything I built — ask first", which is wrong.
**Prevention**:
1. **Tim's role is explicit**: Tim is the CEO, not an engineer. The regression phrases ("OK to commit?", "Want me to merge?", "Should I push?") are banned. The answer is yes by default.
2. **The /review skill is self-completing**: APPROVE → autonomous commit + push + branch + PR + merge-on-green. CHANGES REQUESTED with convergent SHOULD-FIX findings → fix and re-review once, never escalate. Only BLOCK or unfixable CHANGES escalate. `+no-merge` is the explicit opt-out, not the default.
3. **Plan mode scope clarified**: plan mode is for the PROPOSAL only. Once the proposal is approved, implementation is autonomous.
4. **System-state-changing operations** (e.g. starting / stopping / replacing daemons or scheduled jobs) DO require explicit Tim approval. Code commits, PR merges, and repo pushes do NOT.
**Severity**: Tier 1 — directly causes work to not ship, accumulates over weeks.
**Control class**: Known-known — every session knew the rule "default to action" existed but applied it inconsistently because it was a text exhortation rather than baked into the tool.
**Technical enforcement**: /review skill steps for autonomous fix-and-retry and autonomous commit/push/merge. Any future session that ends with an implementation-phase "OK to merge?" is a regression of this pattern.

## Pattern 26: "`from <daemon_module> import` at runtime re-executes the file"
**What happened**: A long-running Python daemon ran as `__main__`. A slice extracted into its own module used `from <daemon_module> import helper` as a lazy import inside a polling path. Python's import machinery re-imported the daemon module (a different name from `__main__`), re-executing every `app.register_blueprint(...)` at module top — Flask refused post-first-request and raised `AssertionError`. The exception was swallowed by an outer try/except that crucially never advanced prev-state. Result: every poll re-detected the same transition and re-broadcast it, producing hours of phantom notifications.
The same anti-pattern almost recurred in the next slice — only caught by reviewer agent at PR review time.
**Why controls missed it**:
1. The synthetic test suite never exercised the polling path — only request-handler routes.
2. Two earlier slices had documented the pitfall in docstrings and used dependency injection instead, but the lessons were not extracted into a generally-applicable pattern. Each new slice rediscovered the issue.
3. The reviewer-agent flagged it on the next slice, but only after the broadcast regression had already shipped to prod for hours.
**Prevention** (technical enforcement):
1. **Never `from <daemon_module> import X` at module-load time** in any extracted slice — it creates a runtime cycle if the daemon runs as `__main__`. Use one of:
   - **Dependency injection** (preferred): the slice exposes `set_server_deps(*, ...)` that the monolith calls at startup. Slice's module-level placeholder names get bound by the monolith and used as callables thereafter.
   - **`sys.modules` lookup at call time** (acceptable when the caller-module is well-known and lookups are rare): `mod = sys.modules.get("__main__") or sys.modules.get("<daemon_module>"); fn = getattr(mod, "_helper", None)`.
   - **Move the helper to a shared module**: if it's genuinely shared infrastructure, it doesn't belong in the daemon's `__main__` file in the first place.
2. **Defence-in-depth on the consumer side**: in any polling loop, advance prev-state BEFORE the broadcast call and wrap the broadcast in its own `try/except`. A future regression that introduces an exception in the broadcast path then can't refire on every poll.
**Severity**: Tier 1 (caused user-visible spam; would have caused full session-handler failure on the next slice if uncaught).
**Control class**: Unknown-known.
**Technical enforcement**: Code-reviewer subagent now blocks `from <daemon_module> import` at module-load time in any new slice review.

## Pattern 27: "Move-only extraction surfaces pre-existing tech debt — fix during the move, not after"
**What happened**: A decomposition plan said "no behaviour change" during slice extractions. Strictly applied, this would have shipped 8+ pre-existing tech-debt items into new files where they'd be harder to find and fix later: token-backup writing alongside live tokens, missing dry-run guards, hardcoded paths, slice-assignment crashes, cross-module mutation. All pre-existing in the monolith. None caught by previous reviews. All caught by the code-reviewer agent at the moment of extraction.
**Why "carry forward" is the wrong default**: The reviewer can SEE the issue in a 100-line diff but couldn't see it in the multi-thousand-line monolith. Carrying it forward into a new file makes the next reviewer's job harder (the issue is now mixed with the move-noise diff and the bug provenance is murkier). Fixing during the move keeps the diff readable and preserves the "this slice is complete" property.
**Prevention** (process):
1. **Reviewer's job during extraction** = (a) verify the move is faithful AND (b) flag pre-existing tech debt now visible in the smaller scope. Tech-debt fixes are in-scope for the slice PR if and only if they're trivially safe (state checks, env-overrides for hardcoded paths, missing guards on already-extracted-elsewhere flags).
2. **Reviewer escalates "fix during move" decisions explicitly**: "I caught X. Fix in this PR or follow-up?" The auto-pilot defaults to "fix in this PR" when the fix is small and the bug is real.
**Severity**: Tier 2 (process — prevents future bugs accumulating, doesn't directly cause damage if violated).
**Control class**: Known-known when applied; previously unknown-known (the §11.5 "no behaviour change" rule existed but was over-applied to "absolutely no behaviour change ever", which let pre-existing bugs ship into new files).
**Technical enforcement**: Reviewer-agent prompt explicitly checks for "issues this slice surfaces but doesn't introduce". Auto-pilot's commit-message template includes a "tech debt fixed during move" section.

## Pattern 28: "Match-anywhere regex on load-bearing path / verb substrings is fragile by construction"
**What happened (combined Pattern 28 + Pattern 30 from the original)**:
1. A PreToolUse hook ran `grep -qE` against the entire Bash command string — including heredoc commit message bodies. When the body contained text like `launchctl kickstart`, `Library/LaunchAgents`, etc., the regex matched and the hook emitted `permissionDecision: ask`. With `skipDangerousModePermissionPrompt: true`, the ask prompt failed closed — Tim never saw a prompt; Claude saw a silent rejection.
2. The first-order fix (an AST-level scanner that strips heredoc bodies before grepping) was incomplete. A `cp /path/to/Library/LaunchAgents/X.plist /elsewhere && diff ...` STILL got rejected because the regex still ran a substring match against the SCAN string and `Library/LaunchAgents` appeared as a **path argument** to `cp` / `diff` / `mv`. The argument was operative — the regex was the bug, not the scanner.
**Cause** (RCA layers):
1. **What happened**: substring matching against a flattened command string cannot distinguish "command executes verb X" from "command argument is a path that mentions X".
2. **Controls existed**: scenario tests for the hook, AST scanner stripping heredocs, the first Pattern 28 documentation entry.
3. **Why each failed**: scenario tests covered "actual writes to <path>" but not "benign read of a file that happens to live under <path>". The AST scanner stripped commit-body DATA but kept positional ARGS unannotated.
4. **Fix classification**: technical enforcement at the AST level. The scanner now classifies positional args by their command's argv shape:
   - `LAST_ARG_IS_DEST = {cp, mv, rsync, install, ln}` — only the last non-flag positional is a write target.
   - `ALL_ARGS_ARE_WRITES = {tee, rm, unlink, rmdir, chmod, chown, chflags, touch, mkdir}` — every non-flag positional is a write target.
   - `WRITE_REDIRECT_TYPES = {>, >>, &>, &>>, >|, 1>, 2>, 1>>, 2>>}` — redirect targets are write targets.
   When a write target arg matches a sensitive path, the scanner emits a sentinel into the SCAN; the hook greps for sentinels rather than raw substrings.
5. **Control class**: known-known after this pattern.
**Generalisable rule**: NEVER match a load-bearing path or verb substring (text that can appear in an argument as data) anywhere in a command's text. Always classify by argument role at AST level: is this arg a write target, a read source, a flag value, a redirect target? Only match against the role being gated. The same anti-pattern covers: commit-message bodies, source paths to `cp`/`mv`, search roots for `find`, content of any `-m "..."` quoted string.
**Severity**: Tier 2 (process — produces silent denials that look like Tim rejecting the tool, costing session time and confidence).
**Technical enforcement**: AST scanner with role-classification + sentinel emission. Comprehensive test suite covering bypass shapes (`bash -c`, `eval`, `python -c`, process substitution, backticks, function defs, env-prefix, subshell, nested heredocs, nested cmdsubst inside `-c`) AND false-positive guards (cp source paths, diff args, search roots, commit-body mentions).

## Pattern 31: "Allowlist gaps under skipDangerousModePermissionPrompt = silent denial"
**What happened**: The harness settings have `skipDangerousModePermissionPrompt: true` and a `permissions.allow` list naming the tools that are permitted without prompting. A tool call returned "user doesn't want to proceed" with no prompt — because the tool was not in the allowlist, and the skip-prompt setting turns "ask Tim" into "silent deny" for un-allowed tools.
**Cause** (RCA layers):
1. **What happened**: harness-level permission check runs before any hook. Tools not in `permissions.allow` would normally prompt; skipDangerousModePermissionPrompt suppresses the prompt and defaults to deny.
2. **Controls existed**: the `permissions.allow` list itself, the skip-prompt flag intended for headless sessions.
3. **Why controls failed**: the allowlist was hand-curated for "tools we use" but never updated when new harness/session-management tools landed. New tools combined with the skip-prompt flag produce a denial that's indistinguishable from a hook rejection.
4. **Fix**: extend the allowlist whenever the harness adds new session-management or read-only tooling. External-effect tools (Bash, Write, Edit, WebFetch) stay individually gated.
5. **Control class**: known-known after this entry.
**Generalisable rule**: `permissions.allow` + `skipDangerousModePermissionPrompt: true` = whitelist. When diagnosing any "user rejected the tool use" with no visible prompt, run two hypotheses in parallel:
- (a) hook regex fired → check audit log + hook source.
- (b) tool not in allowlist → check `settings.json permissions.allow`.
Both look identical from Claude's side.
**Severity**: Tier 2.
**Technical enforcement**: Allowlist extension shipped + this pattern as the differential-diagnosis checklist.

---

## RCA Protocol
**When conducting any RCA, ALWAYS analyse all layers:**
1. **What happened** — sequence of events, immediate cause.
2. **What controls existed** — list every rule, check, or enforcement that should have prevented it.
3. **Why each control failed** — specifically why it didn't fire or was insufficient.
4. **Whether the fix is technical enforcement or another text rule** — if text rule, explain why this one will succeed where previous text rules didn't.
5. **Control class** — known-known (agent knew but skipped), unknown-known (rule exists but agent didn't consult), or unknown-unknown (nobody knew the action was dangerous).

An RCA that only covers layer 1 is incomplete.
