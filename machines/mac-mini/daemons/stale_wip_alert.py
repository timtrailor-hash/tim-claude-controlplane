#!/usr/bin/env python3.11
"""Stale WIP alert. Runs via LaunchAgent every 6 hours.

Detects two conditions in the controlplane repo that indicate abandoned
work-in-progress:

1. Tracked files diverged from HEAD (working tree OR index) whose last
   commit time is older than 24 hours.
2. Local branches with zero unique commits vs origin/main (i.e. fully
   ancestor-merged into main and safe to delete).

NOTE: condition 2 detects ancestor-merged branches only. A branch that
re-implements main with different commits but the same tree content is
NOT flagged — that pattern often represents legitimate parallel work and
gets too many false positives.

When either is found, sends an email alert to Tim — but only if the issue
set has changed since the last successful send, OR ≥24h have elapsed since
the last identical-issue-set send. This prevents 6h-cadence alert spam.
"""

from __future__ import annotations

import hashlib
import json
import smtplib
import ssl
import subprocess
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path.home() / "code"))
from credentials import SMTP_USER, SMTP_PASS  # noqa

REPO = Path.home() / "code" / "tim-claude-controlplane"
STALE_THRESHOLD_SECONDS = 24 * 3600
TO = "timtrailor@gmail.com"
STATE_FILE = Path.home() / "Library" / "Application Support" / "stale_wip_alert" / "state.json"
RESEND_AFTER_SECONDS = 24 * 3600


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO),
        **kw,
    )


def _changed_files() -> list[str]:
    """Tracked files that differ from HEAD: working tree OR index."""
    seen: set[str] = set()
    out: list[str] = []
    for cmd in (["git", "diff", "--name-only"], ["git", "diff", "--cached", "--name-only"]):
        r = _run(cmd)
        if r.returncode != 0:
            print(f"{' '.join(cmd)} failed rc={r.returncode}", file=sys.stderr)
            continue
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if line and line not in seen:
                seen.add(line)
                out.append(line)
    return out


def _last_commit_time(path: str) -> int | None:
    """Unix-time of the last commit that touched `path`. None if never committed."""
    r = _run(["git", "log", "-1", "--format=%ct", "--", path])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def check_uncommitted_changes() -> list[str]:
    """Return tracked files diverged from HEAD whose last-commit time is >24h ago.

    Uses last-commit time (not filesystem mtime) so that touch/sync/build
    activity does not reset the staleness clock.
    """
    files = _changed_files()
    if not files:
        return []

    stale = []
    now = int(time.time())
    for fname in files:
        last_ct = _last_commit_time(fname)
        if last_ct is None:
            # Never-committed (newly staged or first-add). No commit clock
            # exists, so we can't tell if it's been staged for 5 minutes or
            # 5 days. Report it as a "newly staged" item without an age —
            # the dedup fingerprint suppresses repeat sends, so this won't
            # spam. The first run will alert; subsequent runs stay quiet
            # until the issue set changes.
            fpath = REPO / fname
            if fpath.exists():
                stale.append(f"{fname} (newly staged, never committed)")
            continue
        age = now - last_ct
        if age > STALE_THRESHOLD_SECONDS:
            hours = age / 3600
            stale.append(f"{fname} (diverged from HEAD {hours:.0f}h ago)")
    return stale


def check_stale_branches() -> list[str]:
    """Local branches with zero unique commits vs origin/main (fully merged)."""
    fetch = _run(["git", "fetch", "origin", "main", "--quiet"])
    if fetch.returncode != 0:
        print(f"git fetch failed rc={fetch.returncode}, skipping branch check", file=sys.stderr)
        return []

    cur = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    current_branch = cur.stdout.strip() if cur.returncode == 0 else ""

    r = _run(["git", "branch", "--format=%(refname:short)"])
    if r.returncode != 0:
        print(f"git branch failed rc={r.returncode}", file=sys.stderr)
        return []

    stale = []
    for branch in r.stdout.strip().split("\n"):
        branch = branch.strip()
        if not branch or branch == "main" or branch == current_branch:
            continue
        # "fully merged into origin/main": branch has zero commits not on
        # origin/main. This is true ancestry, not tree-content equality.
        rev = _run(["git", "rev-list", "--count", f"origin/main..{branch}"])
        if rev.returncode == 0 and rev.stdout.strip() == "0":
            stale.append(branch)
    return stale


def _fingerprint(stale_files: list[str], stale_branches: list[str]) -> str:
    payload = json.dumps(
        {"files": sorted(stale_files), "branches": sorted(stale_branches)},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _should_send(fingerprint: str, state: dict) -> bool:
    """Send if the issue set changed, or ≥24h since last send of the same set."""
    if state.get("fingerprint") != fingerprint:
        return True
    last_sent = state.get("last_sent_at", 0)
    return (int(time.time()) - last_sent) >= RESEND_AFTER_SECONDS


def send_alert(subject: str, body: str) -> None:
    """Send email. Raises on SMTP failure so launchd records non-zero exit."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = TO

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=60) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    print(f"Alert sent: {subject}")


def main() -> int:
    stale_files = check_uncommitted_changes()
    stale_branches = check_stale_branches()

    if not stale_files and not stale_branches:
        print("No stale WIP found.")
        return 0

    fingerprint = _fingerprint(stale_files, stale_branches)
    state = _load_state()
    if not _should_send(fingerprint, state):
        print(f"Stale WIP unchanged (fingerprint {fingerprint}), skipping send.")
        return 0

    issues = []
    if stale_files:
        issues.append("Uncommitted tracked changes older than 24h:")
        issues.extend(f"  - {f}" for f in stale_files)
    if stale_branches:
        if issues:
            issues.append("")
        issues.append("Local branches with no unique commits vs origin/main:")
        issues.extend(f"  - {b}" for b in stale_branches)

    body = "\n".join(issues)
    body += "\n\nWork was started but never committed/merged. Finish and ship, or revert."

    try:
        send_alert("[controlplane] Stale WIP detected", body)
    except (smtplib.SMTPException, OSError, TimeoutError) as e:
        print(f"Alert send failed: {e}", file=sys.stderr)
        return 1

    state["fingerprint"] = fingerprint
    state["last_sent_at"] = int(time.time())
    _save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
