#!/bin/bash
# Cross-machine ~/.claude/ drift detector.
#
# Compares ~/.claude/{rules,hooks,agents,skills,mcp-launchers}/ and
# settings.json between the local machine and the OTHER machine.
#
# Auto-detects which machine we're on (laptop vs Mac Mini) and SSHes
# to the other one. Reports any divergence to stdout AND to
# ~/.claude/drift_check.log. Sends ntfy alert if any drift exists.
#
# Modes:
#   bash drift_check.sh           # quick mode, log + alert on drift
#   bash drift_check.sh --verbose # also print clean output when no drift
#   bash drift_check.sh --strict  # exit 2 if drift found (for cron / CI)
#
# What's checked:
#   - File presence in each subdir
#   - Content (md5) of every file
#   - settings.json structural keys (mcpServers names, hook types, hook counts)
#
# What's NOT checked (deliberately):
#   - Mutable state: logs, caches, lock files, audit trails
#   - settings.json full content (because some local-only keys are valid)
#   - ~/.claude/projects/ (machine-local conversation history)

set -uo pipefail

LOG=~/.claude/drift_check.log
TS=$(date "+%Y-%m-%d %H:%M:%S")
VERBOSE=0
STRICT=0
for arg in "$@"; do
    case "$arg" in
        --verbose) VERBOSE=1 ;;
        --strict)  STRICT=1 ;;
    esac
done

# Detect which machine we're on
LAPTOP_IP=192.168.0.75
MINI_IP=192.168.0.172
MINI_TAILSCALE=100.126.253.40

HOSTNAME_NOW=$(hostname -s)
case "$HOSTNAME_NOW" in
    *macmini*|*mac-mini*|*mini*) OTHER="$LAPTOP_IP"; OTHER_NAME="laptop" ;;
    *)                            OTHER="$MINI_IP";   OTHER_NAME="mac-mini" ;;
esac

# Verify SSH reachability
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "timtrailor@$OTHER" 'true' 2>/dev/null; then
    # Try Tailscale fallback for Mac Mini
    if [ "$OTHER" = "$MINI_IP" ] && ssh -o ConnectTimeout=5 -o BatchMode=yes "timtrailor@$MINI_TAILSCALE" 'true' 2>/dev/null; then
        OTHER="$MINI_TAILSCALE"
    else
        echo "[$TS] SKIP: $OTHER_NAME ($OTHER) unreachable" | tee -a "$LOG" >&2
        exit 0
    fi
fi

DRIFT=0
REPORT=""

emit() {
    REPORT+="$1
"
    [ "$VERBOSE" = "1" ] && echo "$1"
}

emit "=== drift-check $TS ==="
emit "local: $HOSTNAME_NOW"
emit "remote: $OTHER_NAME ($OTHER)"
emit ""

# 1. Diff each subdir by file md5
for sub in rules hooks agents skills mcp-launchers; do
    LOCAL_DIR="$HOME/.claude/$sub"
    [ -d "$LOCAL_DIR" ] || continue

    # Get local file→md5 map (sorted, only regular files)
    LOCAL_HASHES=$(find -L "$LOCAL_DIR" -type f -not -name "*.log" -not -name "*.cache" -not -name ".DS_Store" -not -name "*.pyc" -not -path "*/__pycache__/*" -not -path "*.pre-deploy-*" 2>/dev/null \
        | sort | while read -r f; do
            REL=${f#$LOCAL_DIR/}
            HASH=$(md5 -q "$f" 2>/dev/null)
            echo "$HASH  $REL"
        done)

    # Get remote file→md5 map via SSH (single round trip)
    REMOTE_HASHES=$(ssh "timtrailor@$OTHER" "cd ~/.claude/$sub 2>/dev/null && find . -type f -not -name '*.log' -not -name '*.cache' -not -name '.DS_Store' -not -name '*.pyc' -not -path '*/__pycache__/*' 2>/dev/null | sort | while read -r f; do REL=\${f#./}; HASH=\$(md5 -q \"\$f\" 2>/dev/null); echo \"\$HASH  \$REL\"; done" 2>/dev/null)

    # Diff the two maps
    LOCAL_NAMES=$(echo "$LOCAL_HASHES" | awk '{print $2}' | sort)
    REMOTE_NAMES=$(echo "$REMOTE_HASHES" | awk '{print $2}' | sort)

    ONLY_LOCAL=$(comm -23 <(echo "$LOCAL_NAMES") <(echo "$REMOTE_NAMES"))
    ONLY_REMOTE=$(comm -13 <(echo "$LOCAL_NAMES") <(echo "$REMOTE_NAMES"))
    BOTH=$(comm -12 <(echo "$LOCAL_NAMES") <(echo "$REMOTE_NAMES"))

    DIVERGED=""
    while IFS= read -r name; do
        [ -z "$name" ] && continue
        LH=$(echo "$LOCAL_HASHES" | awk -v n="$name" '$2==n {print $1}')
        RH=$(echo "$REMOTE_HASHES" | awk -v n="$name" '$2==n {print $1}')
        if [ "$LH" != "$RH" ]; then
            DIVERGED+="$name
"
        fi
    done <<< "$BOTH"

    HAS_DRIFT=0
    [ -n "$ONLY_LOCAL" ] && HAS_DRIFT=1
    [ -n "$ONLY_REMOTE" ] && HAS_DRIFT=1
    [ -n "$DIVERGED" ] && HAS_DRIFT=1

    if [ "$HAS_DRIFT" = "1" ]; then
        DRIFT=1
        emit "## ~/.claude/$sub/ — DRIFT"
        if [ -n "$ONLY_LOCAL" ]; then
            emit "  only on local ($HOSTNAME_NOW):"
            while IFS= read -r f; do [ -n "$f" ] && emit "    + $f"; done <<< "$ONLY_LOCAL"
        fi
        if [ -n "$ONLY_REMOTE" ]; then
            emit "  only on remote ($OTHER_NAME):"
            while IFS= read -r f; do [ -n "$f" ] && emit "    - $f"; done <<< "$ONLY_REMOTE"
        fi
        if [ -n "$DIVERGED" ]; then
            emit "  content differs:"
            while IFS= read -r f; do [ -n "$f" ] && emit "    ~ $f"; done <<< "$DIVERGED"
        fi
        emit ""
    else
        emit "## ~/.claude/$sub/ — clean"
    fi
done

# 2. settings.json structural diff
LOCAL_SETTINGS=~/.claude/settings.json
REMOTE_SETTINGS_TMP=$(mktemp)
ssh "timtrailor@$OTHER" 'cat ~/.claude/settings.json' > "$REMOTE_SETTINGS_TMP" 2>/dev/null

if [ -s "$REMOTE_SETTINGS_TMP" ] && [ -f "$LOCAL_SETTINGS" ]; then
    SETTINGS_DIFF=$(python3 - "$LOCAL_SETTINGS" "$REMOTE_SETTINGS_TMP" "$HOSTNAME_NOW" "$OTHER_NAME" <<'PYEOF'
import json, sys
local = json.load(open(sys.argv[1]))
remote = json.load(open(sys.argv[2]))
local_name = sys.argv[3]
remote_name = sys.argv[4]

issues = []

# MCP servers
local_mcps = set((local.get("mcpServers") or {}).keys())
remote_mcps = set((remote.get("mcpServers") or {}).keys())
if local_mcps - remote_mcps:
    issues.append(f"MCPs only on {local_name}: {sorted(local_mcps - remote_mcps)}")
if remote_mcps - local_mcps:
    issues.append(f"MCPs only on {remote_name}: {sorted(remote_mcps - local_mcps)}")

# Hook commands per stage
def hook_cmds(d):
    out = {}
    for stage, entries in (d.get("hooks") or {}).items():
        cmds = []
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                # Use the script basename as the identity
                tok = next((t for t in cmd.split() if t.startswith("/") and ".sh" in t), cmd[:60])
                cmds.append(tok.rsplit("/",1)[-1])
        out[stage] = sorted(cmds)
    return out

lh = hook_cmds(local)
rh = hook_cmds(remote)
all_stages = set(lh) | set(rh)
for stage in sorted(all_stages):
    lc = set(lh.get(stage, []))
    rc = set(rh.get(stage, []))
    if lc - rc:
        issues.append(f"{stage} hooks only on {local_name}: {sorted(lc - rc)}")
    if rc - lc:
        issues.append(f"{stage} hooks only on {remote_name}: {sorted(rc - lc)}")

# Permissions allow list
la = set(local.get("permissions", {}).get("allow", []))
ra = set(remote.get("permissions", {}).get("allow", []))
if la - ra:
    issues.append(f"permissions.allow only on {local_name}: {sorted(la - ra)}")
if ra - la:
    issues.append(f"permissions.allow only on {remote_name}: {sorted(ra - la)}")

for i in issues:
    print(i)
PYEOF
)
    if [ -n "$SETTINGS_DIFF" ]; then
        DRIFT=1
        emit "## settings.json — STRUCTURAL DRIFT"
        while IFS= read -r line; do
            [ -n "$line" ] && emit "  ! $line"
        done <<< "$SETTINGS_DIFF"
        emit ""
    else
        emit "## settings.json — structurally aligned"
    fi
fi
rm -f "$REMOTE_SETTINGS_TMP"

# 3. Memory topics git repo drift
#
# This catches Pattern 17 at the memory layer: both machines auto-commit to
# their local clone of the memory repo, diverging silently. We compare
# HEAD commits and count how many each side is ahead/behind.
# Canonical memory repo (matches the path CLAUDE.md points at on both machines).
# Only one path — drift between stale clones is outside scope. We verify the
# clone is genuine by checking .git/config exists (catches stray .git dirs).
MEM_REPOS=(
    "$HOME/.claude/projects/-Users-timtrailor-code/memory"
)
for MEM in "${MEM_REPOS[@]}"; do
    [ -f "$MEM/.git/config" ] || continue
    TOPLEVEL=$(git -C "$MEM" rev-parse --show-toplevel 2>/dev/null)
    [ "$TOPLEVEL" = "$MEM" ] || continue
    LOCAL_HEAD=$(git -C "$MEM" rev-parse HEAD 2>/dev/null)
    REMOTE_HEAD=$(ssh -o ConnectTimeout=5 "timtrailor@$OTHER" "[ -f '$MEM/.git/config' ] && git -C '$MEM' rev-parse HEAD 2>/dev/null" 2>/dev/null)

    if [ -z "$REMOTE_HEAD" ]; then
        emit "## memory repo $(basename "$(dirname "$MEM")") — not present on $OTHER_NAME"
        continue
    fi

    if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
        emit "## memory repo — clean (both at ${LOCAL_HEAD:0:8})"
    else
        DRIFT=1
        # Count divergence if origin is fetched
        git -C "$MEM" fetch origin main 2>/dev/null >/dev/null || true
        AHEAD=$(git -C "$MEM" rev-list --count "$REMOTE_HEAD..HEAD" 2>/dev/null || echo "?")
        BEHIND=$(git -C "$MEM" rev-list --count "HEAD..$REMOTE_HEAD" 2>/dev/null || echo "?")
        emit "## memory repo — DRIFT"
        emit "  local  ($HOSTNAME_NOW):  ${LOCAL_HEAD:0:8}  ($AHEAD commits ahead)"
        emit "  remote ($OTHER_NAME):    ${REMOTE_HEAD:0:8}  ($BEHIND commits behind)"
        emit "  Resolve: cd $MEM && git fetch && git merge <other-head>"
        emit ""
    fi
done

# Final
if [ "$DRIFT" = "1" ]; then
    emit "VERDICT: DRIFT detected"
else
    emit "VERDICT: clean"
fi

# Always log
{
    echo ""
    printf '%s' "$REPORT"
    echo "---"
} >> "$LOG"

# Print to stdout if drift OR verbose
if [ "$DRIFT" = "1" ] || [ "$VERBOSE" = "1" ]; then
    [ "$VERBOSE" = "0" ] && printf '%s' "$REPORT"
fi

# ntfy alert
if [ "$DRIFT" = "1" ]; then
    {
        echo "Claude config drift detected ($HOSTNAME_NOW vs $OTHER_NAME)"
        echo "$REPORT" | grep -E "^(##|  [+\-~!])" | head -30
        echo ""
        echo "Run: bash ~/.claude/hooks/drift_check.sh --verbose"
    } | curl -s --max-time 3 -d @- ntfy.sh/timtrailor-claude >/dev/null 2>&1 || true

    [ "$STRICT" = "1" ] && exit 2
fi

exit 0
