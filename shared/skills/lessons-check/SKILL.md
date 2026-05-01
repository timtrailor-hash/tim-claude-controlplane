---
name: lessons-check
description: "Given a proposed change or plan, check it against the patterns in lessons.md and warn if any match. Use before implementing anything that touches daemons, automation, or external systems."
user-invocable: true
disable-model-invocation: false
---

# /lessons-check — pattern-match against past mistakes

The project's `lessons.md` documents recurring failure patterns. Every one has bitten 2+ times. This skill takes a proposed plan or diff and checks if it matches any pattern BEFORE implementation.

## Arguments

`$ARGUMENTS` is the proposed change. Examples:
- `/lessons-check I'm going to add a daemon that auto-restarts failed jobs`
- `/lessons-check <paste of git diff>`
- `/lessons-check` (then read recent context to figure out what's being proposed)

## Steps

### 1. Locate and read lessons.md

Discover the active project's `lessons.md` at runtime — do NOT hardcode a path. The file lives under the current project's memory tree:

```bash
# Discover the project memory dir from CWD or $CLAUDE_PROJECT_DIR
LESSONS=""
if [ -n "$CLAUDE_PROJECT_DIR" ] && [ -f "$CLAUDE_PROJECT_DIR/memory/topics/lessons.md" ]; then
  LESSONS="$CLAUDE_PROJECT_DIR/memory/topics/lessons.md"
else
  # Fall back to the standard layout under ~/.claude/projects/<id>/
  for d in "$HOME/.claude/projects"/*/memory/topics/lessons.md; do
    [ -f "$d" ] && LESSONS="$d" && break
  done
fi
```

If no `lessons.md` is found, output `No lessons.md found in this project — nothing to match against.` and stop.

Re-read every time — don't trust cached knowledge, the file changes.

### 2. Pattern-match

For each pattern in the file, ask: does this proposed change resemble the trigger condition?

Common pattern families to watch for:

| Family | Trigger to watch for |
|---|---|
| Fix creates new problem | Any "automated helper" — daemon, watchdog, recovery, retry loop |
| Safety after happy path | Action-first then guard |
| Silent failures | Health checks that don't test the user-facing feature |
| Fixes that don't stick | 2nd+ attempt at the same fix without technical enforcement |
| Escalating corrections | Re-implementing a rule Tim already corrected before |
| Asking Tim what Claude can do | Plan that says "ask Tim to..." for actions Claude can perform |
| Blocking when away | Plan with mandatory user-input checkpoints in autonomous mode |
| Removing without understanding | Plan that deletes a check/file/daemon without auditing what depends on it |
| Undocumented temp workarounds | Plan that creates a temp service without LaunchAgent or expiry |
| UI for non-deployed builds | Instructions referencing features not in the installed build |

The actual pattern numbers and names come from the live `lessons.md` — use whatever is there.

### 3. For matches, demand mitigation

For each matched pattern, the agent outputs:
```
WARN MATCH: Pattern <N> — <name>
  Trigger: <what in the plan matches>
  Past incidents: <what happened last time>
  Required mitigation: <specific action that must be in the plan>
```

If the plan doesn't already include the required mitigation, BLOCK.

### 4. Daemon / external-action deep-dive (if applicable)

If the plan adds anything that runs as a daemon or sends commands to an external system, the agent makes Tim answer five questions explicitly:

1. What commands can this code send? (List every one.)
2. Does it check state before EVERY action?
3. What happens if the network drops mid-execution?
4. What happens if the target system is in an error state?
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

If no patterns match, the agent outputs `No matches. Proceed.` and stops. Be terse.
