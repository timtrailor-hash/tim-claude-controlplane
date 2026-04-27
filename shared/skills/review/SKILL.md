---
name: review
description: "Run the full pre-commit review pipeline — ruff + semgrep + code-reviewer subagent + dual independent reviewers (Gemini + ChatGPT). On APPROVE, auto-commit + push + PR + merge-on-green. Tim is treated as CEO, not engineer — never asked to review or merge code."
user-invocable: true
disable-model-invocation: false
---

# /review — pre-commit quality gate (triple-reviewer, autonomous merge)

Run Tim's standard code review pipeline AND complete the merge cycle without him. He is not an engineer; asking him to review or merge code is a process failure (Pattern #25). The skill IS the action: review → commit → push → open PR → merge when CI green.

## Pipeline

Three independent reviewers run on every `/review`:

1. **Static analysis** — ruff + semgrep + shellcheck on the changed files
2. **Claude code-reviewer subagent** — applies the lessons.md 8-point checklist
3. **Dual independent reviewers** — Gemini 2.5 Pro AND ChatGPT (GPT-5.4 family) called in parallel for true cross-model review

The combined output is a consolidated APPROVE / CHANGES REQUESTED / BLOCK verdict. **The verdict drives an action, not a question.**

## Arguments

`$ARGUMENTS` controls scope and depth:
- empty / `staged` — review `git diff --cached`
- `unstaged` — review `git diff`
- `head` — review `git show HEAD`
- `<path>` — review a specific file or directory
- `+mini` — force the cheaper model variants (gemini-2.5-flash + gpt-5.4-mini)
- `+full` — force the expensive variants (gemini-2.5-pro + gpt-5.4)
- `+claude-only` — skip Gemini and ChatGPT (cheap mode; NOT allowed for sensitive changes)
- `+deep` — pre-step: run `/deep-context` first on "what has broken in this code area before and why did each fix fail", then pass the synthesis to the reviewers as prior context. Off by default.
- `+no-merge` — run review and report only; do NOT auto-commit/push/merge. Use this only if Tim explicitly asks for a dry-run review.

By default, model selection is auto:
- **<200 changed lines AND not sensitive** → mini variants
- **≥200 changed lines** → full variants
- **Sensitive change** (any line count) → full variants — overrides line count

A change is "sensitive" if `~/.claude/hooks/sensitivity_check.sh` flags it. The classifier looks at:
1. **File paths**: anything matching printer/Klipper/Moonraker, credentials.py, settings.json, ~/.claude/hooks/, ~/.claude/agents/, LaunchAgents/, .plist, mcp-launchers, .github/workflows/, daemons, crontab, sv08/bambu/snapmaker, firmware
2. **Diff content**: dangerous gcode (FIRMWARE_RESTART, SAVE_CONFIG, G28, BED_MESH_CALIBRATE, QUAD_GANTRY_LEVEL, PROBE), `sudo`, `rm -rf`, `DROP TABLE`, `KeepAlive`, `launchctl bootstrap/bootout/load/unload`, hardcoded API keys, BEGIN PRIVATE KEY
3. **File count**: ≥10 files changed → architectural

If any of those match, the review uses full models regardless of line count. False positives are fine — wasting a few cents on a careful review beats missing a real bug.

## Steps

### 0. Optional deep-context pre-step (`+deep` only)

If `$ARGUMENTS` contains `+deep`, before running the normal review pipeline, invoke `/deep-context` with a brief of the form:

> "Changes in `<paths from git diff --stat>` touch an area with historical regressions. Assemble the full prior history: past fix attempts in this area, what was tried, why each attempt failed, cross-cutting patterns, and unresolved threads that touch these files. I need this to evaluate whether the current change addresses the real substrate or patches a symptom."

Save the resulting synthesis. Prepend it to the brief you give each reviewer in step 4 as "HISTORICAL CONTEXT for this area:". The reviewers should evaluate the change against that history, not on its own merits.

Do not run this step if `+deep` is absent. The overhead is only justified when the change is in a recurring-failure area.

### 1. Determine scope and tier

Run `git status` and `git diff --stat` (or matching command for the requested scope). If nothing to review, stop and say so.

Pick the tier in this order (first match wins):
1. If `+full` flag: tier=full
2. If `+mini` flag: tier=mini
3. Run sensitivity classifier: `git diff --name-only HEAD | bash ~/.claude/hooks/sensitivity_check.sh`. If it returns `tier=full reason=...`, use full and surface the reason in the report.
4. Otherwise: count changed lines. ≥200 → full, else mini.

Always print the chosen tier and the reason in the final report.

### 2. Run static analysis

For Python files in scope:
```bash
ruff check <files>
ruff format --check <files>
```

For shell scripts in scope:
```bash
shellcheck -f gcc <files>
```

For Swift files:
```bash
swiftlint lint --quiet <files>
```

For multi-language SAST (only if you have time and the diff is non-trivial):
```bash
semgrep --config=auto --error <files>
```

Collect all findings.

### 3. Run the code-reviewer subagent

Launch the `code-reviewer` subagent via the Agent tool with a clear prompt that includes:
- The scope being reviewed
- The git diff
- A request for the standard verdict format

### 4. Run BOTH independent reviewers in parallel (unless +claude-only)

Launch BOTH reviewers concurrently via two parallel tool calls in a single message:

**Reviewer A — Gemini** (use the `/gemini` skill or call directly):
- Model: `gemini-2.5-pro` (full) or `gemini-2.5-flash` (mini)
- Same context document, same review prompt

**Reviewer B — ChatGPT** (use the `/chatgpt` skill or call directly):
- Model: `gpt-5.4` (full) or `gpt-5.4-mini` (mini)
- Same context document, same review prompt

Use the same review prompt for both so their verdicts are comparable. Write the context once to `/tmp/review_context.md` and reference it from both calls.

If `+claude-only` is set AND the change is sensitive: REFUSE — sensitive changes always require independent reviewers. Print the reason and stop without committing.

### 5. Synthesize all four reviews

Combine into a single report:
```
=== /review report ===
Scope: <files>
Model tier: mini | full
Lines changed: <N>

LINTERS:
  ruff:       <pass | N issues>
  shellcheck: <pass | N issues>
  swiftlint:  <pass | N issues>
  semgrep:    <pass | N issues>

CLAUDE CODE-REVIEWER:  <APPROVE | CHANGES | BLOCK>
  <top findings>

GEMINI 2.5 (model):    <AGREE | PARTIAL | DISAGREE>
  <top findings>

CHATGPT GPT-5.4 (model): <AGREE | PARTIAL | DISAGREE>
  <top findings>

CONVERGENT findings (≥2 reviewers flagged):
  <list>

DIVERGENT findings (only one reviewer flagged):
  <list>

OVERALL: APPROVE | CHANGES REQUESTED | BLOCK
Reasoning: <one paragraph synthesis>
```

### 6. Decision rule

Compute the OVERALL verdict:
- **BLOCK** if ≥2 reviewers say BLOCK, or if any reviewer flags a printer-safety/credential-leak issue, or if any reviewer flags a destructive irreversible operation (data deletion, force-push to main, public-repo secret leak)
- **CHANGES REQUESTED** if ≥2 reviewers say CHANGES, or if any single reviewer says BLOCK
- **APPROVE** if all reviewers APPROVE/AGREE, OR if only NIT-class divergent findings remain

Convergent findings (≥2 reviewers flag the same thing) carry more weight than divergent ones. A single reviewer flagging a real bug is still a real bug — don't dismiss divergent findings, but a lone NIT does not gate APPROVE.

### 7. Autonomous fix-and-retry on CHANGES REQUESTED

If the verdict is CHANGES REQUESTED and the convergent findings are SHOULD-FIX class (not BLOCKERs), apply the fixes yourself and re-run the review pipeline ONCE. Do not bring CHANGES findings to Tim — he is not the engineer. If the fix-and-retry still returns CHANGES or BLOCK, escalate to Tim with a one-paragraph summary of what's left and why you couldn't address it.

If the verdict is BLOCK on the first pass, escalate to Tim immediately with the BLOCKER reason — do not attempt fix-and-retry on a BLOCK.

### 8. Autonomous commit, push, PR, and merge (APPROVE only)

When the final verdict is APPROVE and `+no-merge` is NOT set, complete the merge cycle without asking Tim:

1. **Stage and commit** the reviewed files only (never `git add -A` — pick by name from the scope). Commit message: a one-line summary + a short body explaining why, ending with the trailers:
   ```
   Session: YYYY-MM-DD (<first-8-chars-of-session-uuid>)
   Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
   ```

2. **Push.** If the branch is protected (typical for `main` in `tim-claude-controlplane`):
   - Create a feature branch named `<topic>-<YYYY-MM-DD>` (e.g. `la-turn-hook-restore-2026-04-27`).
   - Push the branch.
   - Open a PR via `gh pr create` from the laptop (Mac Mini's gh auth may be stale; the laptop's `gh` is the canonical actor).
   - Use the review synthesis as the PR body (Summary, Changes, Review verdicts, Test plan).
   - Poll `gh pr view <N> --json statusCheckRollup` until all checks complete.
     - **All SUCCESS + MERGEABLE** → `gh pr merge <N> --squash --delete-branch`. Then `git fetch && git reset --hard origin/main` on the local checkout.
     - **Any FAILURE** → escalate to Tim with the failing check name and link.
     - **TIMEOUT** (>20 min) → escalate to Tim with a "checks still pending" note.

3. If the branch is NOT protected, push directly to the target branch.

4. After merge, post a one-line confirmation to the user with the merge SHA and PR URL. Do NOT ask permission at any point in this flow — that is the regression this rule prevents.

### 9. Sensitive irreversible operations are still gated

The autonomy rule does NOT cover:
- `git push --force` to any branch
- `git reset --hard` on a branch with unique commits
- Public-repo secret-bearing pushes (already blocked by credential_leak_hook, but escalate visibly)
- LaunchAgent state changes (`launchctl bootstrap/bootout/load/unload`) — Pattern 13 still applies

If a /review-driven action would require any of those, stop and surface it as an explicit ask. Pattern 13 wins over Step 8.

## Cost notes

- `+claude-only`: free (your Claude session)
- mini (default for small diffs): ~$0.06 per review (Gemini Flash + GPT-5.4-mini)
- full (default for big diffs): ~$0.15 per review (Gemini Pro + GPT-5.4)
- `+full` on small diffs: same as full

If reviewing a one-line fix, mini is fine. If reviewing today's quality-toolchain remediation work, full is correct.

## Important

- Be terse — no preamble, no narration of intermediate steps unless something fails.
- Never auto-fix linter issues without showing them in the review report (the fix-and-retry in step 7 covers SHOULD-FIX findings, not arbitrary lint).
- ALWAYS run both Gemini and ChatGPT in parallel (two tool calls in a single message). Sequential is wasteful.
- If either reviewer's API fails, continue with the other and note the failure in the synthesis.
- If both reviewers fail on a sensitive change, return CHANGES REQUESTED — never APPROVE without at least one independent reviewer for sensitive code.
- The autonomous merge in step 8 is the default behaviour. `+no-merge` is the explicit opt-out, not the default.
