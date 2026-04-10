#!/bin/bash
# SessionEnd hook — if this session touched ~/.claude/, run drift_check.sh
# in the background and write a tripwire entry to ~/.claude/auto_review.log
# so the next session knows to investigate.
#
# Why background: drift_check.sh SSHes to the other machine which can take
# 5-15 seconds. SessionEnd hooks have short timeouts and we don't want to
# delay session shutdown.
#
# Detection: if any file under ~/.claude/{rules,hooks,agents,skills,mcp-launchers}
# or ~/.claude/settings.json was modified in the last hour, assume the session
# touched it and trigger.

set -uo pipefail

LOG=~/.claude/auto_review.log

# Check for recent modifications under ~/.claude/ (last 60 minutes)
TOUCHED=$(find \
    ~/.claude/rules \
    ~/.claude/hooks \
    ~/.claude/agents \
    ~/.claude/skills \
    ~/.claude/mcp-launchers \
    ~/.claude/settings.json \
    -type f -mmin -60 2>/dev/null \
    -not -name "*.log" -not -name "*.cache" -not -name ".DS_Store" \
    | head -1)

[ -z "$TOUCHED" ] && exit 0

# Something was touched — fire drift_check.sh in the background
nohup bash ~/.claude/hooks/drift_check.sh >> ~/.claude/drift_check.log 2>&1 &

TS=$(date "+%Y-%m-%d %H:%M:%S")
{
    echo "=== $TS — drift_check tripwire fired ==="
    echo "Recent modifications detected under ~/.claude/"
    echo "drift_check.sh is running in background — output: ~/.claude/drift_check.log"
    echo "If drift was detected, ntfy will alert. Run \`bash ~/.claude/hooks/drift_check.sh --verbose\` to inspect."
    echo ""
} >> "$LOG"

exit 0
