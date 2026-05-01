---
name: drift-check
description: "Diff the 3 code locations (laptop ~/Documents/Claude code/, Mac Mini ~/code/, ~/.local/lib/ symlinks) and report divergence. Run before any deploy and after any session that touched code on more than one machine."
user-invocable: true
disable-model-invocation: false
---

# /drift-check — find code drift across machines

Tim's code lives in 3 locations. Changes don't auto-propagate. This skill finds the divergence before it bites.

## Steps

### 1. Identify the canonical pairs

Three pair sets to check (added 2026-04-07 after the missing-hooks incident: a critical drift in `~/.claude/` went undetected for weeks because this skill only covered code dirs):

| Pair | Laptop | Mac Mini |
|---|---|---|
| Code | `/Users/timtrailor/Documents/Claude code/` | `~/code/` |
| Claude config | `~/.claude/{rules,hooks,agents,skills,mcp-launchers}/` + `~/.claude/settings.json` | same path |
| Memory | `~/.claude/projects/-Users-timtrailor-Documents-Claude-code/memory/` | `~/.claude/projects/-Users-timtrailor-code/memory/` |

The Claude config pair is the highest priority — drift there silently breaks every session. The 2026-04-07 review found 3 hook scripts referenced in `settings.json` that existed only on Mac Mini; they had never been synced to laptop.

Some files exist in both, some only in one. Get both file lists:

```bash
# Laptop
find "/Users/timtrailor/Documents/Claude code" -type f \
  \( -name "*.py" -o -name "*.sh" -o -name "*.swift" -o -name "*.md" \) \
  -not -path "*/venv*" -not -path "*/.git/*" -not -path "*/__pycache__/*" \
  | sort > /tmp/laptop_files.txt

# Mac Mini
ssh timtrailor@192.168.0.172 'find ~/code -type f \
  \( -name "*.py" -o -name "*.sh" -o -name "*.swift" -o -name "*.md" \) \
  -not -path "*/venv*" -not -path "*/.git/*" -not -path "*/__pycache__/*"' \
  | sed "s|/Users/timtrailor/code|/Users/timtrailor/Documents/Claude code|" \
  | sort > /tmp/mini_files.txt
```

### 2. Compare hashes for files that exist in both

For each filename present on both machines, compare md5. SSH once with a here-doc to avoid N round-trips:

```bash
comm -12 /tmp/laptop_files.txt /tmp/mini_files.txt > /tmp/common_files.txt
while read f; do
  laptop_hash=$(md5 -q "$f")
  mini_path=${f/Documents\/Claude code/code}
  mini_hash=$(ssh timtrailor@192.168.0.172 "md5 -q '$mini_path' 2>/dev/null")
  [ "$laptop_hash" != "$mini_hash" ] && echo "DRIFT: $f"
done < /tmp/common_files.txt
```

### 3. Check broken symlinks AND ~/.claude/ drift

```bash
ssh timtrailor@192.168.0.172 'find ~/.local/lib -type l ! -exec test -e {} \; -print 2>/dev/null'
ssh timtrailor@192.168.0.172 'find ~/Library/LaunchAgents -name "com.timtrailor.*.plist" -exec grep -l "Documents/Claude code" {} \;'
```

The second command catches LaunchAgents pointing at the laptop-only path — they will exit 2 on Mac Mini.

### 3b. Diff ~/.claude/ between laptop and Mac Mini

This is the highest-priority diff. The 2026-04-07 review found 3 hook scripts referenced in settings.json that existed only on Mac Mini; the laptop had been silently calling missing scripts on every Edit/Write/Bash for an unknown amount of time.

```bash
# Compare ~/.claude/ trees (excluding mutable state)
for sub in rules hooks agents skills mcp-launchers; do
    echo "=== ~/.claude/$sub ==="
    diff -rq \
        ~/.claude/$sub \
        <(ssh timtrailor@192.168.0.172 "tar -C ~/.claude/$sub -cf - ." | tar -tf - 2>/dev/null) \
        2>/dev/null || true
done

# Compare settings.json directly
diff <(cat ~/.claude/settings.json) \
     <(ssh timtrailor@192.168.0.172 'cat ~/.claude/settings.json')

# Run validate_hooks.sh on both machines and compare reports
bash ~/.claude/hooks/validate_hooks.sh
ssh timtrailor@192.168.0.172 'bash ~/.claude/hooks/validate_hooks.sh'
```

Any difference here is high-severity. Surface to Tim immediately and propose a sync direction (usually Mac Mini → laptop, since Tim does most work on Mac Mini).

### 4. Check git status on both repos

```bash
cd "/Users/timtrailor/Documents/Claude code" && git status --short
ssh timtrailor@192.168.0.172 'cd ~/code && git status --short 2>/dev/null'
```

Uncommitted changes in either location = potential drift.

### 5. Report

```
=== Drift Check ===

Files with divergent content (<N>):
- path: <last-modified-laptop> vs <last-modified-mini>

Files only on laptop (<N>):
- ...

Files only on Mac Mini (<N>):
- ...

Broken symlinks (<N>):
- ...

LaunchAgents with bad paths (<N>):
- ...

Uncommitted changes:
- Laptop: <N>
- Mac Mini: <N>

Recommendations:
1. Sync direction: <laptop→mini | mini→laptop | manual>
2. ...
```

### 6. Don't auto-fix

NEVER automatically rsync or overwrite. Report and let Tim choose direction. The wrong sync direction destroys work.

---

## Work-Side Mode (Phase-2 bridge, 2026-05-01)

When `~/.claude/.work-laptop` exists, steps 1-5 above are replaced with a work-specific diff:

### What to compare (work side)

| Pair | Work Laptop | Personal (via bridge) |
|---|---|---|
| Shared rules | `~/.claude/rules/` | Query bridge: `bridge_search_personal_memory(query="inventory shared rules", scope="shared")` |
| Shared skills | `~/.claude/skills/` | List from WORK_ALLOWLIST.yaml allow.skills |
| Work topics | project memory `topics/` | `shared/work-topics/` in controlplane |

### Steps (work side)

1. List all files under `~/.claude/{rules,skills,hooks,agents}/` on the work laptop.
2. Read `WORK_ALLOWLIST.yaml` allow sections to get the expected set of deployed files.
3. Diff: files present locally but not in allowlist (unexpected), files in allowlist but missing locally (deploy gap).
4. For deployed files, compare md5 of local copy vs controlplane source (if the controlplane repo is cloned locally at `~/code/tim-claude-controlplane/`).
5. Report drift with same format as personal-side output.
6. Don't auto-fix. Report and let Tim choose.
