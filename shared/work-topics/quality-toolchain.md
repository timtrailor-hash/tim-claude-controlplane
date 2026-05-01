---
name: Quality Toolchain
description: Code review, static analysis, subagents, and MCP bolt-ons. The standard quality gate for all new code.
type: project
scope: shared
---

# Quality Toolchain

The standard quality gate for any non-trivial code change. Built specifically to prevent the recurring failure patterns in `lessons.md`.

**Why:** Solo dev → no PR review → fixes don't stick → silent failures → automated helpers misbehaving. The toolchain exists to add the technical enforcement that text rules alone cannot guarantee.

**How to apply:** Run `/review` before any commit. Use `code-reviewer` subagent on any daemon/automation change. Use `architect-auditor` before adding a new daemon. Use `second-opinion` (Gemini) and `chatgpt` (current OpenAI model per `WORK_OPENAI_API_KEY`) before any irreversible architectural decision.

## Subagents (sourced from `shared/agents/` in the controlplane, materialised on each machine)
- **code-reviewer** — reads `lessons.md` and applicable safety rules, applies an 8-point checklist, returns APPROVE/CHANGES/BLOCK with line-level findings.
- **architect-auditor** — reads architecture/system topics. Catches recreated-deleted-things, daemon proliferation, code drift, missing doc updates.
- **silent-failure-hunter** — looks specifically for the Pattern 3 shape: code that runs to completion but doesn't actually do the thing (missing error checks, swallowed exceptions, "process running != feature working").
- **second-opinion** — wraps Gemini 2.5 Pro. Independent challenge. Use after code-reviewer for high-stakes changes.
- **chatgpt** — wraps the current OpenAI model (configured in the skill body). Independent third voice; combined with Gemini gives true dual-reviewer coverage.

## Skills (sourced from `shared/skills/` in the controlplane, materialised on each machine)
- **/review** — full pre-commit gate: ruff + semgrep + code-reviewer subagent + Gemini + ChatGPT. On APPROVE, auto-commits, pushes, opens PR, merges on green CI. Tim is treated as CEO not engineer; the pipeline never bounces engineering decisions back upstream.
- **/lessons-check** — given a proposed plan or diff, pattern-matches against `lessons.md` patterns and demands mitigation for matches. Run BEFORE writing code.
- **/audit-daemons** — lists every automated process, verifies referenced paths exist, checks the feature actually works (not just the process running), documents kill switches. Catches Pattern 3.
- **/drift-check** — diffs configured code locations, finds broken symlinks, finds automation entries with bad paths, reports git status. Run before any deploy.

## Hooks (sourced from `shared/hooks/` in the controlplane, deployed to `~/.claude/hooks/` on each machine)
- **lint_hook.sh** — PostToolUse hook on Edit + Write. Runs ruff (Python) + shellcheck (bash) + swiftlint (Swift). All offline, all fast. Semgrep removed from per-edit because `--config=auto` made network calls on every Edit. Logs to `~/.claude/lint_findings.log`. Mode is configurable per machine: blocking once the work side has run advisory for a sprint without false positives.
- **auto_review_hook.sh** — SessionEnd tripwire. Counts ADDED lines (not deletions) per git toplevel, dedupes overlapping cwds, writes to `~/.claude/auto_review.log` if any repo crosses 20 lines.
- **validate_hooks.sh** — SessionStart hook. Parses `settings.json`, verifies every referenced hook script and MCP launcher exists on disk. Sends notification if anything is missing. Catches the silent-MCP-failure class.
- **response-gate** — enforces the response-structure rules (short headline, "done" separated from "decisions needed", ≤2 asks per response).
- **protected_path_hook.sh, credential_leak_hook.sh, audit_log_hook.sh** — shared safety hooks that should be referenced in `settings.json` and exist on disk. The validator catches drift.

## CLI tools installed
- `semgrep` (`brew install semgrep`) — `semgrep --config=auto` for multi-language SAST (used in /review, NOT in per-edit hook).
- `ruff` (`brew install ruff`) — Python lint + format, replaces flake8/isort/bandit/pyupgrade.
- `ccusage` (`npx ccusage@latest`) — token/cost reporting from local JSONL logs.

## MCP servers added to settings.json
- **filesystem** (`@modelcontextprotocol/server-filesystem`) — sandboxed file ops scoped to the working directories.
- **sequential-thinking** (`@modelcontextprotocol/server-sequential-thinking`) — structured reasoning for architecture sessions.
- **context7** (`@upstash/context7-mcp`) — pulls up-to-date library docs into context.
- **serena** (`uvx --from git+https://github.com/oraios/serena`) — symbol-level semantic code navigation across Python and Swift. Addresses drift problems by giving Claude symbol awareness instead of grep-based search.

All run via `npx -y` or `uvx`, no API keys required for these four.

## Hardening lessons baked into the toolchain

- **Wire enforcement into `settings.json`, don't just document it.** Allowlist hooks and protected-path hooks must be referenced in the actual `settings.json` matchers, not only described in a rules file.
- **Validate hook paths every session.** `validate_hooks.sh` parses `settings.json` and checks every referenced script exists. Without this, a missing hook silently exits 127 on every Edit/Write and nothing notices.
- **Safe stdin parsing in hooks.** Replace any pattern that shell-interpolates a JSON `$INPUT` into Python with `printf '%s' "$INPUT" | python3 -c '<no-interpolation>'`. The interpolation form is a code-injection sink.
- **Functional health checks, not process-running checks.** Pattern 3 in `lessons.md`. Memory health = run an actual `search_memory` query and verify it returns results. Same shape for any other MCP/daemon/feature.
- **Pin third-party action SHAs** (e.g. `@v1` → explicit SHA) for supply-chain safety. Drop `push:` triggers on review workflows; PRs only.

## How the pieces fit together

```
Edit a file
   ↓
PostToolUse: lint_hook.sh runs ruff/shellcheck/swiftlint (advisory)
   ↓
Run /review before commit
   ↓
/review runs ruff + semgrep + code-reviewer subagent
   ↓
For daemon/automation code → code-reviewer applies the safety checklist
   ↓
For architectural changes → also architect-auditor
   ↓
For high-stakes → /review fans out to Gemini AND ChatGPT (dual reviewer)
   ↓
On VERDICT == APPROVE → auto commit + push + PR + merge-on-green
```

## Common gotchas
- Serena needs `serena project index` per project on first use; first MCP call after restart will be slow.
- Context7 occasionally rate-limits; falls back gracefully.
- The lint hook runs on every Edit/Write — if it gets noisy, reduce scope by adding more file extensions to the skip list in `lint_hook.sh`.
- Subagents don't share the main session's context — they re-read files each time. Pack subagent prompts with specific paths.
