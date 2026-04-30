#!/usr/bin/env python3.11
"""Tests for scan_command.py — Pattern 28 hook fix + bypass-resistance suite.

Each case has the form (label, command, must_NOT_contain, must_contain).
For each case the scan output:
  - MUST NOT contain any token in `must_NOT_contain`  (false-positive guard)
  - MUST contain every token in `must_contain`        (bypass guard)
"""
import subprocess
import sys
from pathlib import Path

SCAN = str(Path(__file__).parent / "scan_command.py")


def scan(cmd: str) -> str:
    r = subprocess.run(
        ["/opt/homebrew/bin/python3.11", SCAN],
        input=cmd, capture_output=True, text=True, timeout=10,
    )
    return r.stdout


# Verbs we test against. Using launchctl, sudo, and Library/LaunchAgents —
# the three patterns that triggered Pattern 28 in real sessions.
CASES = [
    # ── False-positive guards (data inside -m or quoted heredoc) ──────────
    ("benign-m-quoted",
     "git commit -m 'feat: rotate runbook documents launchctl kickstart usage'",
     ["launchctl"], ["git", "commit"]),

    ("benign-heredoc-singlequoted",
     "git commit -m \"$(cat <<'EOF'\nlaunchctl kickstart in description\nEOF\n)\"",
     ["launchctl"], ["git", "commit"]),

    ("benign-heredoc-singlequoted-tabstripped",
     "git commit -m \"$(cat <<-'EOF'\n\tlaunchctl kickstart in description\n\tEOF\n)\"",
     ["launchctl"], ["git", "commit"]),

    ("benign-sudo-in-m",
     "git commit -m 'document the sudo reboot procedure'",
     ["sudo", "reboot"], ["git", "commit"]),

    ("benign-LA-path-in-m",
     "git commit -m 'note that Library/LaunchAgents holds the plists'",
     ["Library/LaunchAgents"], ["git", "commit"]),

    # ── Original two HIGH bypasses from the prior reviewer ────────────────
    ("bypass-m-cmdsubst",
     'git commit -m "$(launchctl kickstart -k gui/501/foo)"',
     [], ["launchctl", "kickstart"]),

    ("bypass-unquoted-heredoc-cmdsubst",
     "git commit -m \"$(cat <<EOF\n$(launchctl kickstart -k foo)\nEOF\n)\"",
     [], ["launchctl"]),

    # ── New bypasses raised by Gemini Pro + ChatGPT GPT-5.4 ────────────────
    ("bypass-bash-c",
     'bash -c "launchctl kickstart -k foo"',
     [], ["launchctl", "kickstart"]),

    ("bypass-sh-c",
     'sh -c "sudo reboot"',
     [], ["sudo", "reboot"]),

    ("bypass-eval",
     'eval "launchctl kickstart -k foo"',
     [], ["launchctl"]),

    ("bypass-python-c",
     "python3 -c 'import os; os.system(\"launchctl kickstart -k foo\")'",
     [], ["launchctl"]),

    ("bypass-process-substitution-input",
     "diff /etc/passwd <(launchctl kickstart -k foo)",
     [], ["launchctl"]),

    ("bypass-process-substitution-output",
     "tee >(sudo reboot)",
     [], ["sudo", "reboot"]),

    ("bypass-backtick",
     "echo `launchctl kickstart -k foo`",
     [], ["launchctl"]),

    ("bypass-function-define-and-call",
     "f(){ launchctl kickstart -k foo; }; f",
     [], ["launchctl"]),

    ("bypass-env-prefix-bash-c",
     "env FOO=1 bash -c 'launchctl kickstart -k foo'",
     [], ["launchctl"]),

    ("bypass-subshell",
     "( launchctl kickstart -k foo )",
     [], ["launchctl"]),

    # ── True positives that must still be visible ─────────────────────────
    ("plain-launchctl",
     "launchctl kickstart -k gui/501/com.timtrailor.foo",
     [], ["launchctl"]),

    ("compound-real-launchctl",
     "git commit -m 'msg' && launchctl kickstart -k foo",
     [], ["launchctl", "kickstart"]),

    ("real-sudo-reboot",
     "sudo reboot",
     [], ["sudo", "reboot"]),

    ("real-LA-path",
     "ls ~/Library/LaunchAgents",
     [], ["Library/LaunchAgents"]),

    ("real-LA-write",
     "echo foo > ~/Library/LaunchAgents/x.plist",
     [], ["Library/LaunchAgents"]),

    # ── Edge cases ───────────────────────────────────────────────────────
    ("empty", "", [], []),

    ("unparseable",
     'git commit -m "unterminated launchctl kickstart',
     [], ["launchctl"]),  # conservative fallback keeps the verb visible

    ("two-heredocs-one-line",
     # Quoted-EOF1 (literal) + unquoted-EOF2 with launchctl substitution.
     "cat <<'EOF1' && cat <<EOF2\n"
     "plain literal\nEOF1\n"
     "$(launchctl kickstart -k foo)\nEOF2",
     [], ["launchctl"]),

    # ── Bypass shapes the v2 reviewers asked us to prove explicitly ──────
    ("bypass-bash-c-with-cmdsubst",
     'bash -c "$(launchctl kickstart -k foo)"',
     [], ["launchctl"]),

    ("bypass-eval-with-cmdsubst",
     'eval "$(sudo reboot)"',
     [], ["sudo", "reboot"]),

    ("bypass-bash-c-mixed",
     "bash -c 'echo hi; launchctl kickstart -k foo'",
     [], ["launchctl"]),
]


def main() -> int:
    failures = 0
    for label, cmd, forbid, require in CASES:
        out = scan(cmd)
        bad_forbid = [t for t in forbid if t in out]
        bad_require = [t for t in require if t not in out]
        ok = not bad_forbid and not bad_require
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label}")
        if not ok:
            failures += 1
            print(f"  cmd:     {cmd!r}")
            print(f"  scan:    {out!r}")
            if bad_forbid:
                print(f"  must NOT contain (but did): {bad_forbid}")
            if bad_require:
                print(f"  must contain (but didn't):  {bad_require}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
