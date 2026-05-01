#!/usr/bin/env python3
"""work_stale_pr_alert.py — daily stale-PR sweep across work repos.

For every repo visible to the supplied WORK_GITHUB_TOKEN, list open PRs
older than 24 hours. Surfaces:
    {repo, pr_number, title, age_hours, url}

If any are found, push via bridge_push_notification (loaded from
work_mcp_server). If the bridge module is unavailable, drop a JSON
payload into ~/.claude/bridge_outbox/ for the gateway poller to pick
up. As a final defence, /tmp/work_stale_prs.json is always written.

Exits 0 unconditionally so the LaunchAgent does not loop on errors.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
MARKER = HOME / ".claude" / ".work-laptop"
OUT = Path("/tmp/work_stale_prs.json")
LOG = Path("/tmp/work_stale_pr_alert.log")
BRIDGE_OUTBOX = HOME / ".claude" / "bridge_outbox"

REPO_LIMIT = 30
STALE_AGE_HOURS = 24
PR_LIMIT_PER_REPO = 50


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
        r = subprocess.run(["gh", *args], capture_output=True, text=True,
                           env=env, timeout=60)
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
        return [d["nameWithOwner"] for d in json.loads(out)
                if isinstance(d, dict) and d.get("nameWithOwner")]
    except json.JSONDecodeError as e:
        _log(f"repo list parse failed: {e}")
        return []


def _list_open_prs(repo: str, token: str) -> list[dict]:
    rc, out, err = _gh(
        ["pr", "list", "--repo", repo, "--state", "open",
         "--limit", str(PR_LIMIT_PER_REPO),
         "--json", "number,title,createdAt,url,isDraft"],
        token,
    )
    if rc != 0:
        _log(f"pr list failed for {repo}: rc={rc} err={err.strip()[:200]}")
        return []
    try:
        return json.loads(out or "[]")
    except json.JSONDecodeError as e:
        _log(f"pr list parse failed for {repo}: {e}")
        return []


def _hours_since(iso: str) -> float:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return 0.0
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def _try_bridge_push(title: str, body: str) -> tuple[bool, str]:
    """Best-effort: import the work_mcp_server module and call
    bridge_push_notification directly. Returns (sent, detail)."""
    try:
        import importlib.util
        server = HOME / "code" / "claude-bridge" / "tools" / "work_mcp_server.py"
        if not server.is_file():
            return False, f"server file missing at {server}"
        spec = importlib.util.spec_from_file_location("_work_mcp_for_stale_pr", server)
        if spec is None or spec.loader is None:
            return False, "could not build import spec"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "bridge_push_notification"):
            return False, "no bridge_push_notification tool"
        result = mod.bridge_push_notification(title=title, body=body, level="info")
        return True, f"bridge result: {result}"
    except Exception as e:
        return False, f"bridge import/call failed: {e}"


def _drop_outbox(payload: dict) -> tuple[bool, str]:
    try:
        BRIDGE_OUTBOX.mkdir(parents=True, exist_ok=True)
        f = BRIDGE_OUTBOX / f"stale-pr-{uuid.uuid4().hex}.json"
        f.write_text(json.dumps(payload, indent=2))
        return True, f"dropped {f}"
    except Exception as e:
        return False, f"outbox drop failed: {e}"


def main() -> int:
    if not MARKER.exists():
        _log(f"marker {MARKER} missing; exiting 0")
        return 0

    token = _resolve_secret("WORK_GITHUB_TOKEN")
    if not token:
        OUT.write_text(json.dumps({"timestamp": _now_iso(), "error": "no token"}, indent=2))
        _log("WORK_GITHUB_TOKEN missing; wrote no-token marker and exiting 0")
        return 0

    repos = _list_repos(token)
    stale: list[dict] = []
    for r in repos:
        for pr in _list_open_prs(r, token):
            if pr.get("isDraft"):
                continue
            age = _hours_since(pr.get("createdAt", ""))
            if age < STALE_AGE_HOURS:
                continue
            stale.append({
                "repo": r,
                "pr_number": pr.get("number"),
                "title": pr.get("title", ""),
                "age_hours": round(age, 1),
                "url": pr.get("url"),
            })

    payload = {"timestamp": _now_iso(), "stale_prs": stale}
    try:
        OUT.write_text(json.dumps(payload, indent=2))
        _log(f"wrote {OUT} with {len(stale)} stale PR(s)")
    except OSError as e:
        _log(f"write to {OUT} failed: {e}")

    if not stale:
        return 0

    title = f"{len(stale)} stale work PR(s) — open >24h"
    body_lines = [
        f"{p['repo']}#{p['pr_number']} ({p['age_hours']}h): {p['title']}\n  {p['url']}"
        for p in stale[:8]
    ]
    if len(stale) > 8:
        body_lines.append(f"… and {len(stale) - 8} more in {OUT}")
    body = "\n".join(body_lines)

    sent, detail = _try_bridge_push(title, body)
    if sent:
        _log(f"bridge_push_notification ok: {detail}")
    else:
        _log(f"bridge push unavailable ({detail}); falling back to outbox")
        ok, fdetail = _drop_outbox({
            "type": "bridge_push_notification",
            "title": title,
            "body": body,
            "level": "info",
            "stale_prs": stale,
            "created_at": _now_iso(),
        })
        _log(f"outbox: ok={ok} {fdetail}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"fatal: {e}")
        _log(traceback.format_exc())
        sys.exit(0)
