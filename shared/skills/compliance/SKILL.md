---
name: compliance
description: "Show current system compliance score from acceptance_tests.py. Reads /tmp/acceptance_results.json and the compliance_history.jsonl trend. Use whenever Tim asks about system health, the score, or what's failing."
user-invocable: true
disable-model-invocation: false
---

# /compliance — system compliance dashboard

Reads the most recent deterministic acceptance-test run for the active project.
Acceptance tests are expected to run on a schedule via the project's standard automation.
Source: `acceptance_tests.py` in the project root (or `shared/scripts/acceptance_tests.py`).

## What the agent does

1. Read `/tmp/acceptance_results.json`. If the file is older than 3 hours, force a fresh run first:
   ```bash
   python3 acceptance_tests.py
   ```
2. Render the dashboard for Tim: score, per-domain breakdown, list of FAILs with evidence.
3. Read the last 10 entries of `compliance_history.jsonl` (project root) to show the trend (e.g. "score was 87% yesterday, 92% today").
4. For each FAIL, classify as either:
   - **test bug** (wrong endpoint, stale expectation) — propose a fix in `acceptance_tests.py`
   - **real regression** — propose a concrete fix in the underlying system
5. Never invent scores or tests. If the file is missing or unparseable, say so and offer to run the suite manually.

## Do not

- Do not "grade" the system with an LLM opinion when the file exists. The score is whatever the deterministic run produced.
- Do not add new tests to `acceptance_tests.py` in response to this command — that is `/review` territory. Use this skill to read state, not mutate it.

## Related

- `topics/lessons.md` — the silent-failure and stale-monitor patterns are the reason this skill exists. A deterministic acceptance suite is the technical enforcement that prevents both.
