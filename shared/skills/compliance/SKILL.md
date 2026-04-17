---
name: compliance
description: "Show current system compliance score from acceptance_tests.py. Reads /tmp/acceptance_results.json and the compliance_history.jsonl trend. Use whenever Tim asks about system health, the score, or what's failing."
user-invocable: true
disable-model-invocation: false
---

# /compliance — system compliance dashboard

Reads the most recent deterministic acceptance-test run on the Mac Mini.
Acceptance tests run every 2h via `com.timtrailor.acceptance-tests` LaunchAgent.
Source: `~/code/acceptance_tests.py`.

## What to do

1. SSH to Mac Mini if on laptop: `ssh timtrailor@100.126.253.40`.
2. Read `/tmp/acceptance_results.json`. If the file is older than 3 hours, force a fresh run first:
   ```bash
   /opt/homebrew/bin/python3.11 ~/code/acceptance_tests.py
   ```
3. Render the dashboard for Tim: score, per-domain breakdown, list of FAILs with evidence.
4. Read the last 10 entries of `~/code/compliance_history.jsonl` to show the trend (e.g. "score was 87% yesterday, 92% today").
5. For each FAIL, classify as either:
   - **test bug** (wrong endpoint, stale expectation) — propose a fix in `~/code/acceptance_tests.py`
   - **real regression** — propose a concrete fix in the underlying system
6. Never invent scores or tests. If the file is missing or unparseable, say so and offer to run the suite manually.

## Do not

- Do not "grade" the system with an LLM opinion when the file exists. The score is whatever the deterministic run produced.
- Do not add new tests to `acceptance_tests.py` in response to this command — that's `/review` territory. Use this skill to read state, not mutate it.

## Related

- `~/.claude/plans/streamed-dazzling-orbit.md` — the 2026-04-03 design doc that prescribed this framework.
- `topics/lessons.md` Pattern 3 (silent failures) and Pattern 20 (stale monitor).
