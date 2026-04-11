"""
Printer safety scenario tests — exercises printer_safety.py hook.

Every historical printer failure from lessons.md must have a named test here.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"
# Use env var for printer IP to avoid false positives from the hook scanning heredocs
PRINTER_IP = os.environ.get("TEST_PRINTER_IP", "192.168.0.108")


def _run_printer_hook(command_str):
    """Run printer-safety-check.sh with a simulated Bash tool input."""
    hook_input = {"tool_input": {"command": command_str}}
    proc = subprocess.run(
        ["bash", str(HOOKS_DIR / "printer-safety-check.sh")],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.stdout, proc.stderr, proc.returncode


def _build_gcode_url(gcode):
    """Build a curl command targeting the test printer with given gcode."""
    return f'curl -s http://{PRINTER_IP}:7125/printer/gcode/script?script={gcode}'


class TestAlwaysBlocked:
    """Commands that should ALWAYS be blocked regardless of printer state.
    Source: lessons.md Pattern 1, Pattern 2, Pattern 5."""

    def test_prn001_firmware_restart_blocked(self):
        """PRN-001: destroyed 12h+ prints multiple times."""
        _, stderr, rc = _run_printer_hook(_build_gcode_url("FIRMWARE_RESTART"))
        assert rc == 2, f"Should be blocked (rc={rc})"
        assert "deny" in stderr.lower() or "block" in stderr.lower()

    def test_prn002_restart_blocked(self):
        """PRN-002: RESTART is equivalent to FIRMWARE_RESTART."""
        _, stderr, rc = _run_printer_hook(_build_gcode_url("RESTART"))
        assert rc == 2, f"Should be blocked (rc={rc})"


class TestNonPrinterPassthrough:
    """Commands not targeting a printer must pass through untouched."""

    def test_prn020_non_printer_curl(self):
        """Non-printer curl should be allowed."""
        _, _, rc = _run_printer_hook("curl -s http://example.com/api")
        assert rc == 0

    def test_prn021_normal_bash(self):
        """Normal bash commands should pass through."""
        _, _, rc = _run_printer_hook("ls -la /tmp")
        assert rc == 0

    def test_prn022_ssh_non_printer(self):
        """SSH to a non-printer IP should pass through."""
        _, _, rc = _run_printer_hook("ssh user@192.168.0.172 ls")
        assert rc == 0
