#!/usr/bin/env python3.11
"""Stale WIP alert. Runs via LaunchAgent every 6 hours.

Detects two conditions in the controlplane repo that indicate abandoned
work-in-progress:

1. Uncommitted tracked changes older than 24 hours.
2. Local branch whose commits are already in origin/main (stale branch).

When either is found, sends an email alert to Tim.
"""
from __future__ import annotations

import smtplib
import ssl
import subprocess
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, "/Users/timtrailor/code")
from credentials import SMTP_USER, SMTP_PASS  # noqa

REPO = Path.home() / "code" / "tim-claude-controlplane"
STALE_THRESHOLD_SECONDS = 24 * 3600
TO = "timtrailor@gmail.com"


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=30,
        cwd=str(REPO), **kw,
    )


def check_uncommitted_changes() -> list[str]:
    """Return list of tracked files with uncommitted changes older than 24h."""
    r = _run(["git", "diff", "--name-only"])
    if r.returncode != 0 or not r.stdout.strip():
        return []

    stale = []
    now = time.time()
    for fname in r.stdout.strip().split("\n"):
        fpath = REPO / fname
        if not fpath.exists():
            continue
        mtime = fpath.stat().st_mtime
        age = now - mtime
        if age > STALE_THRESHOLD_SECONDS:
            hours = age / 3600
            stale.append(f"{fname} (modified {hours:.0f}h ago)")
    return stale


def check_stale_branches() -> list[str]:
    """Return list of local branches whose content is already on origin/main."""
    _run(["git", "fetch", "origin", "main", "--quiet"])
    r = _run(["git", "branch", "--format=%(refname:short)"])
    if r.returncode != 0:
        return []

    stale = []
    for branch in r.stdout.strip().split("\n"):
        branch = branch.strip()
        if not branch or branch == "main":
            continue
        diff = _run(["git", "diff", f"origin/main..{branch}", "--stat"])
        if diff.returncode == 0 and not diff.stdout.strip():
            stale.append(branch)
    return stale


def send_alert(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = TO

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    print(f"Alert sent: {subject}")


def main() -> None:
    issues = []

    stale_files = check_uncommitted_changes()
    if stale_files:
        issues.append("Uncommitted tracked changes older than 24h:")
        for f in stale_files:
            issues.append(f"  - {f}")

    stale_branches = check_stale_branches()
    if stale_branches:
        issues.append("Local branches with content already on origin/main:")
        for b in stale_branches:
            issues.append(f"  - {b}")

    if not issues:
        print("No stale WIP found.")
        return

    body = "\n".join(issues)
    body += "\n\nThis means work was started but never committed/merged."
    body += " Either finish and ship it, or revert it."
    send_alert("[controlplane] Stale WIP detected", body)


if __name__ == "__main__":
    main()
