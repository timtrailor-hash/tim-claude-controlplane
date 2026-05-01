#!/usr/bin/env python3
"""work_ci_failure_poller.py — hourly poll of GitHub Actions on work repos.

Scope: every repo the user can see via `gh repo list --visibility=all
--limit 20`. We use whatever GH account is authenticated by the supplied
WORK_GITHUB_TOKEN. We do NOT hard-code repo names — the work-laptop's
repo set will drift over time and we want this to keep pace.

For each repo we list workflow runs in the last 6 h whose conclusion is
`failure`. Each surface row contains:
    repo, workflow, run_url, timestamp, head_sha

Output: /tmp/work_ci_failures.json
    {"timestamp": "<ISO>", "failures": [...]}
or, if WORK_GITHUB_TOKEN is missing:
    {"timestamp": "<ISO>", "error": "no token"}

Exit 0 unconditionally. Errors become file content, not non-zero exits.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
MARKER = HOME / ".claude" / ".work-laptop"
OUT = Path("/tmp/work_ci_failures.json")
LOG = Path("/tmp/work_ci_failure_poller.log")

LOOKBACK_HOURS = 6
REPO_LIMIT = 20
RUN_LIMIT_PER_REPO = 25


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_secret(name: str) -> str | None:
    val = os.environ.get(name)
    if val:
        return val
    try:
        r = subprocess.run(
            ["security", "find-generic-password",
             "-a", name, "-s", "tim-credentials", "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        _log(f"keychain lookup for {name} failed: {e}")
        return None
    if r.returncode != 0:
        return None
    val = r.stdout.strip()
    return val or None


def _gh(args: list[str], token: str) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    try:
        r = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, env=env, timeout=60,
        )
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "gh CLI not installed"
    except subprocess.TimeoutExpired:
        return 124, "", "gh timed out"
    except Exception as e:
        return 1, "", f"{e}"


def _list_repos(token: str) -> list[str]:
    rc, out, err = _gh(
        ["repo", "list", "--visibility=all", "--limit", str(REPO_LIMIT),
         "--json", "nameWithOwner"],
        token,
    )
    if rc != 0:
        _log(f"repo list failed: rc={rc} err={err.strip()}")
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        _log(f"repo list parse failed: {e}")
        return []
    return [d["nameWithOwner"] for d in data if isinstance(d, dict) and d.get("nameWithOwner")]


def _list_failed_runs(repo: str, token: str, cutoff: datetime) -> list[dict]:
    rc, out, err = _gh(
        ["api",
         f"repos/{repo}/actions/runs?status=failure&per_page={RUN_LIMIT_PER_REPO}",
         "-q", ".workflow_runs"],
        token,
    )
    if rc != 0:
        _log(f"runs list failed for {repo}: rc={rc} err={err.strip()[:200]}")
        return []
    try:
        runs = json.loads(out or "[]")
    except json.JSONDecodeError as e:
        _log(f"runs parse failed for {repo}: {e}")
        return []
    out_rows: list[dict] = []
    for run in runs or []:
        try:
            ts = run.get("updated_at") or run.get("created_at")
            if not ts:
                continue
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t < cutoff:
                continue
            if (run.get("conclusion") or "").lower() != "failure":
                continue
            out_rows.append({
                "repo": repo,
                "workflow": run.get("name") or run.get("path") or "unknown",
                "run_url": run.get("html_url"),
                "timestamp": ts,
                "head_sha": run.get("head_sha"),
            })
        except Exception as e:
            _log(f"row parse failed for {repo}: {e}")
            continue
    return out_rows


def main() -> int:
    if not MARKER.exists():
        _log(f"marker {MARKER} missing; exiting 0")
        return 0

    token = _resolve_secret("WORK_GITHUB_TOKEN")
    if not token:
        OUT.write_text(json.dumps({"timestamp": _now_iso(), "error": "no token"}, indent=2))
        _log("WORK_GITHUB_TOKEN missing; wrote no-token marker and exiting 0")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    repos = _list_repos(token)
    _log(f"found {len(repos)} repo(s) to check")

    failures: list[dict] = []
    for r in repos:
        failures.extend(_list_failed_runs(r, token, cutoff))

    payload = {"timestamp": _now_iso(), "failures": failures}
    try:
        OUT.write_text(json.dumps(payload, indent=2))
        _log(f"wrote {OUT} with {len(failures)} failure(s) across {len(repos)} repo(s)")
    except OSError as e:
        _log(f"write to {OUT} failed: {e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"fatal: {e}")
        _log(traceback.format_exc())
        sys.exit(0)
