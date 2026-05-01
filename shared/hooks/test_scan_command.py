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

    # ── Pattern-28 second-order fix (2026-05-01) ────────────────────────
    # The protected_path_hook used to grep for `Library/LaunchAgents`
    # anywhere in the operative tokens. That tripped on PATH-ARGUMENT
    # mentions inside read-only or source-position args. The scanner now
    # emits `__LA_WRITE__` ONLY when the path appears in a write position
    # (cp/mv DEST, tee/rm/chmod arg, `>`/`>>` redirect target).

    # 1. Tim's exact failing case (2026-05-01): a benign cp+diff between
    #    his Library/LaunchAgents and a controlplane repo path.
    #    Path components broken into f-string fragments to stay <=120 chars.
    ("la-cp-from-LA-to-repo-then-diff",
     (lambda src, dst: f"cp {src} {dst} && diff {src} {dst}")(
         "/Users/timtrailor/Library/LaunchAgents/com.timtrailor.stale-pr-alert.plist",
         "/Users/timtrailor/code/tim-claude-controlplane/machines/mac-mini"
         "/launchagents/com.timtrailor.stale-pr-alert.plist",
     ),
     ["__LA_WRITE__"], ["cp", "diff"]),

    # 2. cp with LA path as SOURCE (read), non-LA dest → no write marker.
    ("la-cp-source-only",
     "cp /Users/x/Library/LaunchAgents/x.plist /tmp/dst.plist",
     ["__LA_WRITE__"], ["cp"]),

    # 3. mv with LA path as SOURCE, non-LA dest → no write marker.
    ("la-mv-source-only",
     "mv /Users/x/Library/LaunchAgents/x.plist /tmp/dst.plist",
     ["__LA_WRITE__"], ["mv"]),

    # 4. cat of an LA plist → no write marker.
    ("la-cat-readonly",
     "cat /Users/x/Library/LaunchAgents/x.plist",
     ["__LA_WRITE__"], ["cat"]),

    # 5. diff between two LA paths → no write marker.
    ("la-diff-readonly",
     "diff /Users/x/Library/LaunchAgents/a.plist /Users/x/Library/LaunchAgents/b.plist",
     ["__LA_WRITE__"], ["diff"]),

    # 6. cp with LA path as DEST (a real install) → write marker MUST fire.
    ("la-cp-dest-is-LA",
     "cp /tmp/src.plist /Users/x/Library/LaunchAgents/x.plist",
     [], ["__LA_WRITE__"]),

    # 7. rm of an LA path → write marker MUST fire.
    ("la-rm-target",
     "rm /Users/x/Library/LaunchAgents/x.plist",
     [], ["__LA_WRITE__"]),

    # 8. tee to an LA path → write marker MUST fire.
    ("la-tee-target",
     "tee /Users/x/Library/LaunchAgents/x.plist",
     [], ["__LA_WRITE__"]),

    # 9. `>` redirect to LA path → write marker MUST fire.
    ("la-redirect-write",
     "echo foo > /Users/x/Library/LaunchAgents/x.plist",
     [], ["__LA_WRITE__"]),

    # 10. launchctl bootstrap with LA path as arg → no write marker
    #     (launchctl is not in LAST_ARG_IS_DEST or ALL_ARGS_ARE_WRITES;
    #     Pattern 2 of the hook handles state-changing verbs separately,
    #     so we don't need a write-marker here, and emitting one would be
    #     redundant but harmless).
    ("launchctl-bootstrap-arg-is-LA",
     "launchctl bootstrap gui/501 /Users/x/Library/LaunchAgents/x.plist",
     ["__LA_WRITE__"], ["launchctl", "bootstrap"]),

    # 11. bash -c wrapping launchctl bootstrap with LA path → still
    #     surfaces launchctl + bootstrap so Pattern 2 can match.
    ("launchctl-bootstrap-via-bash-c",
     "bash -c 'launchctl bootstrap gui/501 ~/Library/LaunchAgents/x.plist'",
     [], ["launchctl", "bootstrap"]),

    # 12. eval-wrapped launchctl bootout → still visible via eval recursion.
    ("launchctl-bootout-via-eval",
     'eval "launchctl bootout gui/501/com.timtrailor.x"',
     [], ["launchctl", "bootout"]),

    # ── /etc and /Library system-path narrowing ─────────────────────────
    # Same anti-pattern existed in Pattern 5: `cp .* /Library/` matched
    # any `/Library/` substring, so a path like /Users/x/Library/y also
    # tripped. The scanner now emits __SYS_WRITE__ only when the write
    # target STARTS with /etc/ or /Library/.

    # 13. cp from a user-home Library path to /tmp → no SYS marker.
    ("sys-cp-user-library-to-tmp",
     "cp /Users/x/Library/Preferences/foo.plist /tmp/foo.plist",
     ["__SYS_WRITE__"], ["cp"]),

    # 14. cp into /Library/Caches → SYS marker MUST fire.
    ("sys-cp-into-Library",
     "cp /tmp/x /Library/Caches/x",
     [], ["__SYS_WRITE__"]),

    # 15. redirect into /etc/hosts → SYS marker MUST fire.
    ("sys-redirect-etc",
     "echo foo > /etc/hosts",
     [], ["__SYS_WRITE__"]),
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
