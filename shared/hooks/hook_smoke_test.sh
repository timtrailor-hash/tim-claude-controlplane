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
            echo "FAIL [$label]: expected PASS but hook emitted permissionDecision" >&2
            echo "  command: $command" >&2
            echo "  output:  $output" >&2
            FAILURES=$((FAILURES + 1))
            return
        fi
    elif [ "$expected" = "ask" ]; then
        if ! echo "$output" | grep -q '"permissionDecision":[[:space:]]*"ask"'; then
            echo "FAIL [$label]: expected ASK but got something else" >&2
            echo "  command: $command" >&2
            echo "  output:  $output" >&2
            FAILURES=$((FAILURES + 1))
            return
        fi
    elif [ "$expected" = "deny" ]; then
        if ! echo "$output" | grep -q '"permissionDecision":[[:space:]]*"deny"'; then
            echo "FAIL [$label]: expected DENY but got something else" >&2
            echo "  command: $command" >&2
            echo "  output:  $output" >&2
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
    # Pattern 36 / four-tier model: these don't match the safe-verb bypass
    # (cp/find not in the list), and Pattern 1-7 detectors don't match
    # (cp /tmp target, find without -delete). The new dispatch routes them
    # to T3 reviewer. In production with subscription Claude, the reviewer
    # judges them APPROVE. Smoke test runs with SKIP_TIER3_REVIEWER=1 so
    # the reviewer returns ASK without making an API call — this asserts
    # the dispatch is wired correctly, not the reviewer's final verdict.
    run_hook "$HOOK" "cp ~/Library/LaunchAgents/x.plist /tmp/backup.plist" "ask" "cp-from-launchagents-tier3"
    run_hook "$HOOK" "find ~/Library/LaunchAgents -name '*.plist'" "ask" "find-launchagents-readonly-tier3"
    run_hook "$HOOK" "jq . /tmp/test.json" "pass" "jq-readonly"

    # MUST PASS: piped and chained read-only commands (Pattern-34 targets)
    run_hook "$HOOK" "grep -n 'Pattern 28' lessons.md | head -20" "pass" "grep-pipe-head"
    run_hook "$HOOK" "tail -80 lessons.md | grep Pattern" "pass" "tail-pipe-grep"
    run_hook "$HOOK" "ps aux | grep claude | grep -v grep | head -10" "pass" "ps-multi-pipe"
    run_hook "$HOOK" "cd ~/code && git status" "pass" "cd-chain-status"
    run_hook "$HOOK" "cd ~/code && git diff -- protected_path_hook.sh" "pass" "cd-chain-diff"
    run_hook "$HOOK" 'sleep 0 && cat /tmp/test.txt && echo done' "pass" "sleep-chain-cat"
    run_hook "$HOOK" "gh pr view 38 --json statusCheckRollup" "pass" "gh-pr-view"
    run_hook "$HOOK" 'grep -n "__LA_WRITE__\|LaunchAgents" scan_command.py | head -20' "pass" "grep-sentinel-pipe"

    # MUST ASK: genuinely dangerous commands
    run_hook "$HOOK" "launchctl kickstart -k gui/501/com.timtrailor.test" "ask" "launchctl-kickstart"
    run_hook "$HOOK" "launchctl bootstrap gui/501 ~/Library/LaunchAgents/test.plist" "ask" "launchctl-bootstrap"
    run_hook "$HOOK" "sudo reboot" "ask" "sudo-reboot"
    # Force-push to main: under four-tier model = T2 deny (catastrophic for
    # shared history). Old policy (Pattern 7) was ask; new policy denies
    # outright. Force-push to non-main branches is T4 (ask), tested below.
    run_hook "$HOOK" "git push --force origin main" "deny" "git-push-force-main"

    # ADVERSARIAL: bypass attempts that must NOT pass through the read-only shortcut
    run_hook "$HOOK" 'ls $(cp evil.plist ~/Library/LaunchAgents/test.plist)' "ask" "cmd-subst-bypass"
    run_hook "$HOOK" 'cp test.plist ~/Library/LaunchAgents/ && echo done' "ask" "cp-la-chain"
    run_hook "$HOOK" 'echo test > /Library/LaunchAgents/evil.plist' "ask" "redirect-la-bypass"
    run_hook "$HOOK" 'cp /tmp/x /Library/test.plist' "ask" "cp-sys-write"

    # ADVERSARIAL: process substitution must not bypass via safe-verb wrapper
    run_hook "$HOOK" 'cat <(cp evil.plist ~/Library/LaunchAgents/test.plist)' "ask" "proc-subst-bypass"
    run_hook "$HOOK" 'diff <(cat /etc/hosts) <(cp evil.plist /Library/LaunchDaemons/x.plist)' "ask" "proc-subst-pair-bypass"

    # ADVERSARIAL: spaceless operators must fall through residual check, then
    # be caught by the full scan because they write to a protected path.
    run_hook "$HOOK" 'cd ~/code&&cp evil.plist ~/Library/LaunchAgents/' "ask" "spaceless-and-la-write"
    run_hook "$HOOK" 'echo foo|cp evil.plist ~/Library/LaunchAgents/' "ask" "spaceless-pipe-cp"

    # Pattern 36: spaceless pipe trips the residual metacharacter check, so
    # bypass fails and command goes to T3 reviewer. In production reviewer
    # would APPROVE; in skip mode it returns ASK.
    run_hook "$HOOK" 'ls|head -3' "ask" "spaceless-pipe-head-tier3"

    # ── Four-tier classifier — explicit T2 (auto-deny) cases ─────────────
    # T2 = catastrophic, no human, no reviewer. Hook emits permissionDecision
    # "deny" without round-tripping through the reviewer.
    run_hook "$HOOK" "rm -rf /" "deny" "t2-rm-rf-root"
    run_hook "$HOOK" 'rm -rf /*' "deny" "t2-rm-rf-wildcard-root"
    run_hook "$HOOK" "git push --force origin main" "deny" "t2-force-push-main"
    run_hook "$HOOK" 'sed -i "s/x/y/" ~/.claude/hooks/protected_path_hook.sh' "deny" "t2-hook-self-edit"

    # ── Four-tier classifier — explicit T4 (Tim must tap) cases ──────────
    # T4 = irreversible-and-visible. Pattern 1-7 already cover most; the new
    # additions are visibility flips, package installs, paid spend.
    run_hook "$HOOK" "gh repo edit foo/bar --visibility public" "ask" "t4-gh-repo-public"
    run_hook "$HOOK" "brew install some-package" "ask" "t4-brew-install"
    run_hook "$HOOK" "pip install requests" "ask" "t4-pip-install"
    run_hook "$HOOK" "git push --force origin feat/some-branch" "ask" "t4-force-push-non-main"
fi

if [ "$FAILURES" -gt 0 ]; then
    echo "hook_smoke_test: $FAILURES FAILURES, $PASSES passed" >&2
    exit 1
else
    echo "hook_smoke_test: all $PASSES tests passed"
    exit 0
fi
