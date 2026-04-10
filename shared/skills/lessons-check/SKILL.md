---
name: lessons-check
description: "Given a proposed change or plan, check it against the 10 patterns in lessons.md and warn if any match. Use before implementing anything that touches the printer, daemons, or external systems."
user-invocable: true
disable-model-invocation: false
---

# /lessons-check — pattern-match against past mistakes

Tim's `lessons.md` documents 10 recurring failure patterns. Every one has bitten 2+ times. This skill takes a proposed plan or diff and checks if it matches any pattern BEFORE implementation.

## Arguments

`$ARGUMENTS` is the proposed change. Examples:
- `/lessons-check I'm going to add a daemon that auto-resumes failed prints`
- `/lessons-check <paste of git diff>`
- `/lessons-check` (then read recent context to figure out what's being proposed)

## Steps

### 1. Read lessons.md

```
~/.claude/projects/-Users-timtrailor-Documents-Claude-code/memory/topics/lessons.md
```

Load all 10 patterns. Re-read every time — don't trust cached knowledge, the file changes.

### 2. Pattern-match

For each pattern, ask: does this proposed change resemble the trigger condition?

| Pattern | Trigger to watch for |
|---|---|
| 1. Fix creates new problem | Any "automated helper" — daemon, watchdog, recovery, retry loop |
| 2. Safety after happy path | Action-first then guard |
| 3. Silent failures | Health checks that don't test the user-facing feature |
| 4. Fixes that don't stick | 2nd+ attempt at the same fix without technical enforcement |
| 5. Escalating corrections | Re-implementing a rule Tim already corrected before |
| 6. Asking Tim what Claude can do | Plan that says "ask Tim to..." for SSH/file/run actions |
| 7. Blocking when away | Plan with mandatory user-input checkpoints in autonomous mode |
| 8. Removing without understanding | Plan that deletes a check/file/daemon without auditing what depends on it |
| 9. Undocumented temp workarounds | Plan that creates a temp service without LaunchAgent or expiry |
| 10. UI for non-deployed builds | iOS instructions referencing features not in Tim's installed build |

### 3. For matches, demand mitigation

For each matched pattern, output:
```
⚠ MATCH: Pattern <N> — <name>
  Trigger: <what in the plan matches>
  Past incidents: <what happened last time>
  Required mitigation: <specific action that must be in the plan>
```

If the plan doesn't already include the required mitigation, BLOCK.

### 4. Pattern 1 deep-dive (if applicable)

If Pattern 1 matches (anything that sends commands to the printer or runs as a daemon), make Tim answer the 5 questions explicitly:

1. What commands can this code send? (List every one.)
2. Does it check `print_stats.state` before EVERY action?
3. What happens if the network drops mid-execution?
4. What happens if Klipper is in error state?
5. Can Tim stop it with a single command?

If any answer is "I don't know", BLOCK with `STOP — answer the 5 questions before writing code`.

### 5. Output

```
=== /lessons-check ===

Plan: <one-line summary>

Patterns matched: <N>

<for each match, the block above>

VERDICT: PROCEED | PROCEED WITH CONDITIONS | STOP

Conditions (if any):
- ...
```

If no patterns match, output `No matches. Proceed.` and stop. Be terse.
