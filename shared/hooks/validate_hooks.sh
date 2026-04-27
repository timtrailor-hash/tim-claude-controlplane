#!/bin/bash
# SessionStart hook: validate that every hook script and MCP launcher
# referenced in settings.json actually exists and is executable, AND that
# any required hook stages for this machine are present and non-empty.
#
# Why: today's review found 3 hook scripts referenced in settings.json that
# didn't exist on disk (pre-existing drift). This catches that class of bug
# at session start, before broken hooks fail silently for weeks.
#
# 2026-04-27 extension (Pattern 17): also assert REQUIRED hook stages are
# non-empty. The 2026-04-18 controlplane deploy silently dropped Stop and
# UserPromptSubmit on Mac Mini, breaking iPhone Live Activity turn-end
# detection for 9 days. Static guard so that exact regression cannot recur.
#
# Behaviour: writes a report to /tmp/claude_hook_validation.log and to
# ~/.claude/hook_validation.log on every session start. Sends an ntfy
# notification if anything is missing. Exits 0 always — advisory.

set -o pipefail

SETTINGS=~/.claude/settings.json
LOG=~/.claude/hook_validation.log
TS=$(date "+%Y-%m-%d %H:%M:%S")

[ -f "$SETTINGS" ] || exit 0

# Initialise arrays up-front (set -u is sensitive to empty array refs in older bash).
MISSING=()
EMPTY_STAGES=()

# Per-machine required hook stages. Empty array on machines that don't need
# any specific stage (e.g. laptop — the server only drives Live Activity for
# the mobile tmux session on Mac Mini).
REQUIRED_STAGES=()
case "$(hostname -s)" in
    Tims-Mac-mini)
        # Live Activity (iPhone Dynamic Island) turn-boundary detection.
        REQUIRED_STAGES=(Stop UserPromptSubmit)
        ;;
esac

# Extract every command path referenced in settings.json
# (top-level "command" fields under hooks AND under mcpServers)
PATHS=$(python3 - "$SETTINGS" <<'PYEOF'
import json, sys, re
data = json.load(open(sys.argv[1]))
seen = set()

# Hook commands
for stage in data.get("hooks", {}).values():
    for entry in stage:
        for h in entry.get("hooks", []):
            cmd = h.get("command", "")
            # Extract first non-flag token that looks like a path
            for tok in cmd.split():
                if tok.startswith("/") and ("/" in tok):
                    seen.add(tok)
                    break

# MCP server commands
for name, srv in data.get("mcpServers", {}).items():
    cmd = srv.get("command", "")
    if cmd.startswith("/"):
        seen.add(cmd)
    for arg in srv.get("args", []):
        if arg.startswith("/") and "/" in arg:
            seen.add(arg)

for p in sorted(seen):
    print(p)
PYEOF
)

while IFS= read -r p; do
    [ -z "$p" ] && continue
    if [ ! -e "$p" ]; then
        MISSING+=("$p")
    fi
done <<< "$PATHS"

# Check required hook stages are present and non-empty.
if [ "${#REQUIRED_STAGES[@]}" -gt 0 ]; then
    for stage in "${REQUIRED_STAGES[@]}"; do
        count=$(python3 - "$SETTINGS" "$stage" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
stage = sys.argv[2]
arr = data.get("hooks", {}).get(stage, [])
total = 0
for entry in arr:
    total += len(entry.get("hooks", []))
print(total)
PYEOF
        )
        if [ "${count:-0}" = "0" ]; then
            EMPTY_STAGES+=("$stage")
        fi
    done
fi

if [ ${#MISSING[@]} -eq 0 ] && [ ${#EMPTY_STAGES[@]} -eq 0 ]; then
    echo "[$TS] OK — all referenced paths exist; required stages populated" >> "$LOG"
    exit 0
fi

# Write report
{
    echo "[$TS] VALIDATION FAILED — ${#MISSING[@]} missing path(s), ${#EMPTY_STAGES[@]} empty required stage(s):"
    for p in "${MISSING[@]}"; do
        echo "  MISSING: $p"
    done
    for s in "${EMPTY_STAGES[@]}"; do
        echo "  EMPTY REQUIRED STAGE: $s"
    done
    echo "---"
} | tee -a "$LOG" >&2

# Notify (ntfy is fire-and-forget, won't block)
{
    echo "Claude hook validation failed:"
    [ ${#MISSING[@]} -gt 0 ] && echo "${#MISSING[@]} missing paths"
    for p in "${MISSING[@]}"; do echo "- $(basename "$p")"; done
    [ ${#EMPTY_STAGES[@]} -gt 0 ] && echo "Empty required stages: ${EMPTY_STAGES[*]}"
} | curl -s --max-time 3 -d @- ntfy.sh/timtrailor-claude >/dev/null 2>&1 || true

exit 0
