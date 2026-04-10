#!/usr/bin/env python3
"""Test suite for printer_safety.py PreToolUse hook.

Run: python3 ~/.claude/hooks/test_printer_safety.py
Or:  pytest ~/.claude/hooks/test_printer_safety.py -v

Tests the hook by feeding it mock stdin and checking exit codes + stderr output."""

import subprocess, json, sys, os, tempfile

HOOK = os.path.expanduser("~/.claude/hooks/printer_safety.py")
PYTHON = sys.executable

PASS = 0
FAIL = 0


def run_hook(command_str, expect_blocked=False, label=""):
    """Run the hook with a mock Bash tool_input and check the result."""
    global PASS, FAIL

    hook_input = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command_str}
    })

    result = subprocess.run(
        [PYTHON, HOOK],
        input=hook_input,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )

    blocked = result.returncode == 2
    status = "BLOCKED" if blocked else "ALLOWED"
    expected = "BLOCKED" if expect_blocked else "ALLOWED"

    if blocked == expect_blocked:
        PASS += 1
        icon = "PASS"
    else:
        FAIL += 1
        icon = "FAIL"

    print(f"  {icon}: {label} — expected {expected}, got {status}")
    if icon == "FAIL":
        print(f"        stderr: {result.stderr[:200]}")
        print(f"        exit: {result.returncode}")

    return blocked


print("=" * 60)
print("PRINTER SAFETY HOOK — TEST SUITE")
print("=" * 60)

# ============================================================
# 1. Non-printer commands should ALWAYS pass through
# ============================================================
print("\n--- Non-printer commands (should all pass) ---")
run_hook("ls -la", expect_blocked=False, label="Plain ls")
run_hook("curl -s http://example.com", expect_blocked=False, label="Curl to non-printer")
run_hook("ssh sovol@192.168.0.108 ls", expect_blocked=False, label="SSH to printer (not gcode)")
run_hook("curl http://192.168.0.108:7125/printer/objects/query?print_stats", expect_blocked=False, label="Status query (no gcode/script)")

# ============================================================
# 2. FIRMWARE_RESTART — ALWAYS blocked regardless of state
# ============================================================
print("\n--- FIRMWARE_RESTART (always blocked) ---")
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=FIRMWARE_RESTART"',
    expect_blocked=True,
    label="FIRMWARE_RESTART on SV08"
)
run_hook(
    'curl -s "http://192.168.0.69:7125/printer/gcode/script?script=FIRMWARE_RESTART"',
    expect_blocked=True,
    label="FIRMWARE_RESTART on Snapmaker"
)
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=RESTART"',
    expect_blocked=True,
    label="RESTART on SV08"
)

# ============================================================
# 3. Fail-closed: malformed gcode commands should be blocked
# ============================================================
print("\n--- Fail-closed (malformed commands) ---")
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script"',
    expect_blocked=True,
    label="gcode/script with no script= param"
)
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script="',
    expect_blocked=True,
    label="Empty script= value"
)

# ============================================================
# 4. Safe commands (M117 etc) — should be allowed
#    Note: these hit the real Moonraker API to check state.
#    If printer is unreachable, allowlisted commands still pass.
# ============================================================
print("\n--- Safe allowlisted commands ---")
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=M117%20Hello"',
    expect_blocked=False,
    label="M117 display message"
)
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=M220%20S100"',
    expect_blocked=False,
    label="M220 speed factor"
)
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=PAUSE"',
    expect_blocked=False,
    label="PAUSE"
)
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=CANCEL_PRINT_CONFIRMED"',
    expect_blocked=False,
    label="CANCEL_PRINT_CONFIRMED"
)

# ============================================================
# 5. Dangerous commands — blocked during printing
#    (If printer is currently printing, these should be blocked.
#     If standby, they'll pass. We test the always-blocked ones above.)
# ============================================================
print("\n--- URL-encoded commands ---")
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=FIRMWARE_RESTART"',
    expect_blocked=True,
    label="URL-encoded FIRMWARE_RESTART"
)
run_hook(
    'curl -s "http://192.168.0.108:7125/printer/gcode/script?script=M117%20test%20message"',
    expect_blocked=False,
    label="URL-encoded M117 with spaces"
)

# ============================================================
# 6. Unknown printer IPs should pass through (not our concern)
# ============================================================
print("\n--- Unknown printer IPs (not monitored) ---")
run_hook(
    'curl -s "http://192.168.0.200:7125/printer/gcode/script?script=FIRMWARE_RESTART"',
    expect_blocked=False,
    label="Unknown IP — not in config"
)

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 60)
TOTAL = PASS + FAIL
print(f"RESULTS: {PASS}/{TOTAL} passed, {FAIL} failed")
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {FAIL}")
print("=" * 60)

sys.exit(0 if FAIL == 0 else 1)
