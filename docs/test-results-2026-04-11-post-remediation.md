# Post-Remediation Test Results — 2026-04-11

## Machine: Mac Mini (Tims-Mac-mini.local)
## Date: 2026-04-11 10:14 BST
## Context: Run during autonomous remediation of 6 audit findings

## 1. Scenario Test Suite
**Command:** `pytest scenarios/ -v`
**Python:** 3.11.14, pytest 9.0.3
**Result:** 43 passed in 3.31s

```
scenarios/credentials/test_credential_safety.py    — 3 passed
scenarios/drift/test_drift_detection.py            — 4 passed
scenarios/hooks/test_hooks_wiring.py               — 6 passed
scenarios/memory/test_memory_health.py             — 3 passed
scenarios/printer/test_printer_safety.py           — 5 passed
scenarios/test_behavioral.py                       — 14 passed
scenarios/test_conversation_server.py              — 8 passed
```

## 2. verify.sh
**Command:** `./verify.sh`
**Result:** 13 passed, 0 failed, 0 warnings (exit 0)

## 3. drift_check.sh
**Command:** `shared/hooks/drift_check.sh`
**Result:** Clean (exit 0, no output)

## 4. validate_hooks.sh
**Command:** `~/.claude/hooks/validate_hooks.sh`
**Result:** Clean (exit 0)

## Laptop Results (Tims-MacBook-Pro-2)
- **verify.sh:** 11 passed, 0 failed, 1 warning (pytest not installed)
- **validate_hooks.sh:** 1 missing path (`-Users-timtrailor-Documents-Claude-code/memory`)
  — expected, laptop does not use `~/Documents/Claude code/` working directory
