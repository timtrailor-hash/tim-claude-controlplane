#!/usr/bin/env python3
"""work_credential_rotation.py — quarterly rotation reminder.

Tracked secrets (work-side, all in macOS Keychain under service
`tim-credentials`):
    - WORK_OPENAI_API_KEY
    - WORK_GEMINI_API_KEY
    - WORK_GITHUB_TOKEN

These are vendor-issued tokens; we cannot rotate them automatically.
This script fires on Day 1 of every quarter, queries each entry's
modification timestamp out of `security find-generic-password -g`,
formats a Tim-readable checklist, and sends a bridge_push_notification.

Day-of-quarter gate: launchd plist already only fires on Jan/Apr/Jul/
Oct day 1, but if launchd ever fires this entry off-cadence (clock
skew, time-warp), the script's own date check still gates the send.

Exits 0 on every error path.
"""
from __future__ import annotations

import json
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
MARKER = HOME / ".claude" / ".work-laptop"
LOG = Path("/tmp/work_credential_rotation.log")
BRIDGE_OUTBOX = HOME / ".claude" / "bridge_outbox"

TRACKED = [
    "WORK_OPENAI_API_KEY",
    "WORK_GEMINI_API_KEY",
    "WORK_GITHUB_TOKEN",
]
QUARTER_MONTHS = (1, 4, 7, 10)


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_quarter_start() -> bool:
    now = datetime.now(timezone.utc)
    return now.month in QUARTER_MONTHS and now.day == 1


def _keychain_modified_at(name: str) -> str | None:
    """Pull the keychain entry's mdat (modification date) from the
    `-g` output. Format example:
        "mdat"<timedate>=0x32303236303330323230353030305A00  "20260302205000Z\\000"
    We extract the trailing ZULU stamp and reformat to ISO-8601.
    """
    try:
        r = subprocess.run(
            ["security", "find-generic-password",
             "-a", name, "-s", "tim-credentials", "-g"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        _log(f"keychain lookup for {name} failed: {e}")
        return None
    # security writes the keychain attributes to stderr.
    blob = (r.stderr or "") + "\n" + (r.stdout or "")
    if r.returncode != 0:
        return None

    # Look for the mdat line and pull the YYYYMMDDhhmmssZ string.
    for line in blob.splitlines():
        if '"mdat"' not in line:
            continue
        # Take the segment between the last pair of double quotes.
        parts = line.rsplit('"', 2)
        if len(parts) < 3:
            continue
        candidate = parts[-2]
        # Strip any trailing nulls (\000) and split off the Z.
        candidate = candidate.split("\\000")[0].rstrip()
        if len(candidate) >= 15 and candidate.endswith("Z"):
            try:
                t = datetime.strptime(candidate, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)
                return t.isoformat()
            except ValueError:
                continue
    return None


def _try_bridge_push(title: str, body: str) -> tuple[bool, str]:
    try:
        import importlib.util
        server = HOME / "code" / "claude-bridge" / "tools" / "work_mcp_server.py"
        if not server.is_file():
            return False, f"server file missing at {server}"
        spec = importlib.util.spec_from_file_location("_work_mcp_for_cred_rot", server)
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
        f = BRIDGE_OUTBOX / f"cred-rot-{uuid.uuid4().hex}.json"
        f.write_text(json.dumps(payload, indent=2))
        return True, f"dropped {f}"
    except Exception as e:
        return False, f"outbox drop failed: {e}"


def main() -> int:
    if not MARKER.exists():
        _log(f"marker {MARKER} missing; exiting 0")
        return 0

    if not _is_quarter_start():
        _log(f"not a quarter start (today is {datetime.now(timezone.utc).date()}); exiting 0")
        return 0

    rows: list[str] = []
    for name in TRACKED:
        mdat = _keychain_modified_at(name)
        if mdat:
            rows.append(f"- {name}: last set {mdat}")
        else:
            rows.append(f"- {name}: no keychain entry / no mdat (set it now)")

    title = "Quarterly credential rotation reminder"
    body = (
        "Rotate the work-side API keys this quarter:\n\n"
        + "\n".join(rows)
        + "\n\nReplace each in the keychain via:"
        + "\n  security add-generic-password -a <NAME> -s tim-credentials -w '<value>' -U"
    )

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
            "tracked": TRACKED,
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
