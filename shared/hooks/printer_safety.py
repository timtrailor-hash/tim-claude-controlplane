#!/usr/bin/env python3
"""PreToolUse hook: Allowlist-based printer command safety.

Reads printer IPs and safety rules from ~/.claude/printer_config.toml.
FAIL-CLOSED: If a command targets a printer but we can't parse it, we BLOCK."""

import json
import sys
import re
import urllib.parse
import subprocess
import os
from datetime import datetime, timezone

CONFIG_PATH = os.path.expanduser("~/.claude/printer_config.toml")
LOG_FILE = os.path.expanduser("~/.claude/printer_audit.log")


def load_config():
    """Load printer config. Returns (printer_ips, always_blocked, allowlist)."""
    # Parse TOML without external deps (Python 3.11+ has tomllib)
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            # Fallback: hardcoded defaults if no TOML parser available
            return (
                {"192.168.0.108", "192.168.0.69"},
                {"FIRMWARE_RESTART", "RESTART"},
                {"M117", "SET_GCODE_OFFSET", "M220", "M221", "SET_FAN_SPEED",
                 "PAUSE", "RESUME", "CANCEL_PRINT_CONFIRMED"},
            )

    try:
        with open(CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        # Config missing — log warning but use safe defaults
        # (hook must still function if config hasn't been created yet)
        audit_log("?", "?", "?", "WARN: printer_config.toml not found, using defaults")
        return (
            {"192.168.0.108", "192.168.0.69"},
            {"FIRMWARE_RESTART", "RESTART"},
            {"M117", "SET_GCODE_OFFSET", "M220", "M221", "SET_FAN_SPEED",
             "PAUSE", "RESUME", "CANCEL_PRINT_CONFIRMED"},
        )
    except Exception as e:
        # Config exists but is corrupt — fail-closed, block everything
        audit_log("?", "?", "?", f"ERROR: printer_config.toml corrupt: {e}")
        return (set(), {"FIRMWARE_RESTART", "RESTART"}, set())

    printer_ips = set()
    for name, printer in cfg.get("printers", {}).items():
        ip = printer.get("ip")
        if ip:
            printer_ips.add(ip)

    safety = cfg.get("safety", {})
    always_blocked = set(safety.get("always_blocked", ["FIRMWARE_RESTART", "RESTART"]))
    allowlist = set(safety.get("printing_allowlist", []))

    return printer_ips, always_blocked, allowlist


def deny(reason):
    """Block the command with a deny decision."""
    msg = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }
    print(json.dumps(msg), file=sys.stderr)
    sys.exit(2)


def audit_log(state, ip, cmd, result):
    """Append to audit log. Never fails."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} | state={state} | ip={ip} | cmd={cmd} | result={result}\n")
    except Exception:
        pass


# --- Load config ---
PRINTER_IPS, ALWAYS_BLOCKED, ALLOWED = load_config()

# Build IP regex from config
ip_pattern = "|".join(re.escape(ip) for ip in PRINTER_IPS)

# --- Parse hook input ---
raw = sys.stdin.read()
try:
    data = json.loads(raw)
    command = data.get("tool_input", {}).get("command", "")
except Exception:
    sys.exit(0)  # Not a valid hook input — not our concern

# --- Only check commands targeting configured printer IPs with gcode endpoint ---
if not re.search(rf"({ip_pattern}).*gcode/script", command):
    sys.exit(0)  # Not a printer gcode command — allow

# From here on, we KNOW this is a printer gcode command.
# Any failure to parse = BLOCK (fail-closed).

ip_match = re.search(rf"({ip_pattern})", command)
if not ip_match:
    audit_log("?", "?", "?", "BLOCKED (no IP match after initial match)")
    deny("BLOCKED: Detected printer gcode command but could not extract IP. Fail-closed.")

printer_ip = ip_match.group(1)

# Extract gcode (handles ?script=X and -d "script=X")
gcode_match = re.search(r'script=([^\s"&\']+)', command)
if not gcode_match:
    audit_log("?", printer_ip, "?", "BLOCKED (no gcode extracted)")
    deny(f"BLOCKED: Detected gcode command to {printer_ip} but could not extract gcode. Fail-closed.")

gcode = urllib.parse.unquote(gcode_match.group(1)).strip().split()[0].upper()
if not gcode:
    audit_log("?", printer_ip, "(empty)", "BLOCKED (empty gcode)")
    deny(f"BLOCKED: Extracted empty gcode for {printer_ip}. Fail-closed.")

# --- Commands ALWAYS blocked regardless of state ---
if gcode in ALWAYS_BLOCKED:
    audit_log("any", printer_ip, gcode, "BLOCKED_ALWAYS")
    deny(
        f"BLOCKED: '{gcode}' requires Tim's explicit permission regardless of "
        f"printer state. This is a hard safety rule — do NOT retry without asking Tim."
    )

# --- Check printer state ---
try:
    result = subprocess.run(
        ["curl", "-s", "--max-time", "3",
         f"http://{printer_ip}:7125/printer/objects/query?print_stats"],
        capture_output=True, text=True, timeout=5
    )
    state = json.loads(result.stdout)["result"]["status"]["print_stats"]["state"]
except Exception:
    state = "unknown"

# --- Determine verdict ---
if state in ("printing", "paused"):
    if gcode in ALLOWED:
        verdict = "ALLOWED"
    else:
        verdict = "BLOCKED"
elif state == "unknown":
    # State unknown — allow safe commands, block risky ones
    if gcode in ALLOWED:
        verdict = "ALLOWED (state=unknown, cmd in allowlist)"
    else:
        verdict = "BLOCKED (state=unknown, cmd not in allowlist)"
else:
    verdict = f"ALLOWED (state={state})"

audit_log(state, printer_ip, gcode, verdict)

if verdict.startswith("BLOCKED"):
    if "unknown" in verdict:
        deny(
            f"BLOCKED: Cannot determine printer state (Moonraker unreachable?) and "
            f"'{gcode}' is not in the safe allowlist. Fail-closed. "
            f"Safe commands during unknown state: {', '.join(sorted(ALLOWED))}"
        )
    else:
        deny(
            f"BLOCKED: '{gcode}' not in allowlist for state '{state}'. "
            f"Allowed: {', '.join(sorted(ALLOWED))}."
        )

sys.exit(0)
