#!/bin/bash
# hook_smoke_test.sh — Meta-defence for PreToolUse hook false positives.
# Run at SessionStart to catch regressions before Tim hits them live.
# Pattern-34: expanded from 21 to 29 tests covering pipes, chains, and
# the grep-sentinel edge case.

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILURES=0
PASSES=0

make_input() {
    python3 -c "
import json, sys
cmd = sys.argv[1]
print(json.dumps({'tool_name':'Bash','tool_input':{'command': cmd}}))
" "$1"
}

run_hook() {
    local hook="$1" command="$2" expected="$3" label="$4"
    local input output
    input=$(make_input "$command")
    output=$(echo "$input" | bash "$hook" 2>/dev/null) || true

    if [ "$expected" = "pass" ]; then
        if echo "$output" | grep -q '"permissionDecision"'; then
            echo "FAIL [$label]: expected PASS but hook emitted ask/deny" >&2
            echo "  command: $command" >&2
            FAILURES=$((FAILURES + 1))
            return
        fi
    elif [ "$expected" = "ask" ]; then
        if ! echo "$output" | grep -q '"permissionDecision"'; then
            echo "FAIL [$label]: expected ASK but hook passed through" >&2
            echo "  command: $command" >&2
            FAILURES=$((FAILURES + 1))
            return
        fi
    fi
    PASSES=$((PASSES + 1))
}

# ── Dependency checks ────────────────────────────────────────────────────

if ! python3 -c "import bashlex" 2>/dev/null; then
    echo "FAIL [bashlex-dependency]: bashlex not installed — scan_command.py will use regex fallback" >&2
    echo "  Fix: pip3 install --user --break-system-packages bashlex" >&2
    FAILURES=$((FAILURES + 1))
else
    PASSES=$((PASSES + 1))
fi

# ── protected_path_hook.sh tests ─────────────────────────────────────────

HOOK="$HOOK_DIR/protected_path_hook.sh"
if [ -f "$HOOK" ]; then
    # MUST PASS: read-only simple commands
    run_hook "$HOOK" "grep LaunchAgents lessons.md" "pass" "grep-launchagents"
    run_hook "$HOOK" "tail -20 scan_command.py" "pass" "tail-scan-command"
    run_hook "$HOOK" "cat ~/Library/LaunchAgents/com.timtrailor.test.plist" "pass" "cat-launchagent"
    run_hook "$HOOK" "ls ~/Library/LaunchAgents/" "pass" "ls-launchagents"
    run_hook "$HOOK" "diff a.plist b.plist" "pass" "diff-files"
    run_hook "$HOOK" "git add credential_leak_hook.sh protected_path_hook.sh" "pass" "git-add-hooks"
    run_hook "$HOOK" "git diff HEAD -- hooks/protected_path_hook.sh" "pass" "git-diff-hooks"
    run_hook "$HOOK" "git log --oneline -5" "pass" "git-log"
    run_hook "$HOOK" "git status" "pass" "git-status"
    run_hook "$HOOK" "ssh timtrailor@100.126.253.40 ls ~/Library/LaunchAgents/" "pass" "ssh-ls-launchagents"
    run_hook "$HOOK" "launchctl list" "pass" "launchctl-list-readonly"
    run_hook "$HOOK" "launchctl print gui/501" "pass" "launchctl-print-readonly"
    run_hook "$HOOK" "echo hello world" "pass" "echo-simple"
    run_hook "$HOOK" "cp ~/Library/LaunchAgents/x.plist /tmp/backup.plist" "pass" "cp-from-launchagents"
    run_hook "$HOOK" "find ~/Library/LaunchAgents -name '*.plist'" "pass" "find-launchagents-readonly"
    run_hook "$HOOK" "jq . /tmp/test.json" "pass" "jq-readonly"

    # MUST PASS: piped and chained read-only commands (Pattern-34 targets)
    run_hook "$HOOK" "grep -n 'Pattern 28' lessons.md | head -20" "pass" "grep-pipe-head"
    run_hook "$HOOK" "tail -80 lessons.md | grep Pattern" "pass" "tail-pipe-grep"
    run_hook "$HOOK" "ps aux | grep claude | grep -v grep | head -10" "pass" "ps-multi-pipe"
    run_hook "$HOOK" "cd ~/code && git status" "pass" "cd-chain-status"
    run_hook "$HOOK" "cd ~/code && git diff -- protected_path_hook.sh" "pass" "cd-chain-diff"
    run_hook "$HOOK" 'sleep 5 && cat /tmp/test.txt && echo done' "pass" "sleep-chain-cat"
    run_hook "$HOOK" "gh pr view 38 --json statusCheckRollup" "pass" "gh-pr-view"
    run_hook "$HOOK" 'grep -n "__LA_WRITE__\|LaunchAgents" scan_command.py | head -20' "pass" "grep-sentinel-pipe"

    # MUST ASK: genuinely dangerous commands
    run_hook "$HOOK" "launchctl kickstart -k gui/501/com.timtrailor.test" "ask" "launchctl-kickstart"
    run_hook "$HOOK" "launchctl bootstrap gui/501 ~/Library/LaunchAgents/test.plist" "ask" "launchctl-bootstrap"
    run_hook "$HOOK" "sudo reboot" "ask" "sudo-reboot"
    run_hook "$HOOK" "git push --force origin main" "ask" "git-push-force"

    # ADVERSARIAL: bypass attempts that must NOT pass through the read-only shortcut
    run_hook "$HOOK" 'ls $(cp evil.plist ~/Library/LaunchAgents/test.plist)' "ask" "cmd-subst-bypass"
    run_hook "$HOOK" 'cp test.plist ~/Library/LaunchAgents/ && echo done' "ask" "cp-la-chain"
    run_hook "$HOOK" 'echo test > /Library/LaunchAgents/evil.plist' "ask" "redirect-la-bypass"
    run_hook "$HOOK" 'cp /tmp/x /Library/test.plist' "ask" "cp-sys-write"
fi

if [ "$FAILURES" -gt 0 ]; then
    echo "hook_smoke_test: $FAILURES FAILURES, $PASSES passed" >&2
    exit 1
else
    echo "hook_smoke_test: all $PASSES tests passed"
    exit 0
fi
