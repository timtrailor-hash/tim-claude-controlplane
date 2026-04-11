"""
Hook wiring scenario tests — verify settings.json references real files.

Source: 2026-04-07 review found 3 hooks referenced but missing on disk.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


@pytest.fixture
def settings():
    with open(SETTINGS_PATH) as f:
        return json.load(f)


class TestHookWiring:
    """Every hook referenced in settings.json must exist on disk."""

    def test_cfg001_all_hook_paths_exist(self, settings):
        """CFG-001: every command path in hooks must resolve to a real file."""
        missing = []
        for stage_name, entries in settings.get("hooks", {}).items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    for tok in cmd.split():
                        if tok.startswith("/") and "/" in tok[1:]:
                            if not os.path.exists(tok):
                                missing.append(f"{stage_name}: {tok}")
                            break
        assert len(missing) == 0, f"Missing hook paths: {missing}"

    def test_cfg002_all_mcp_commands_exist(self, settings):
        """CFG-002: every MCP server command must exist."""
        missing = []
        for name, srv in settings.get("mcpServers", {}).items():
            cmd = srv.get("command", "")
            if cmd.startswith("/") and not os.path.exists(cmd):
                missing.append(f"{name}: {cmd}")
        assert len(missing) == 0, f"Missing MCP commands: {missing}"

    def test_cfg003_printer_safety_wired(self, settings):
        """CFG-003: printer-safety-check MUST be wired in PreToolUse:Bash.
        Source: 2026-04-07 found it was unwired for weeks."""
        found = False
        for entry in settings.get("hooks", {}).get("PreToolUse", []):
            for hook in entry.get("hooks", []):
                if "printer-safety-check" in hook.get("command", ""):
                    found = True
        assert found, "printer-safety-check.sh not wired in PreToolUse:Bash!"

    def test_cfg004_credential_leak_hook_wired(self, settings):
        """CFG-004: credential_leak_hook must be wired for Write and Edit."""
        matchers_with_cred = set()
        for entry in settings.get("hooks", {}).get("PreToolUse", []):
            for hook in entry.get("hooks", []):
                if "credential_leak" in hook.get("command", ""):
                    matchers_with_cred.add(entry.get("matcher", ""))
        assert "Write" in matchers_with_cred, "credential_leak not wired for Write"
        assert "Edit" in matchers_with_cred, "credential_leak not wired for Edit"

    def test_cfg005_validate_hooks_in_session_start(self, settings):
        """CFG-005: validate_hooks.sh must run at SessionStart."""
        found = False
        for entry in settings.get("hooks", {}).get("SessionStart", []):
            for hook in entry.get("hooks", []):
                if "validate_hooks" in hook.get("command", ""):
                    found = True
        assert found, "validate_hooks.sh not in SessionStart hooks"


class TestHookExecutability:
    """Hook scripts must be executable."""

    def test_cfg010_all_hooks_executable(self):
        """CFG-010: every .sh file in hooks/ must be chmod +x."""
        hooks_dir = Path.home() / ".claude" / "hooks"
        non_executable = []
        for f in hooks_dir.glob("*.sh"):
            if not os.access(f, os.X_OK):
                non_executable.append(f.name)
        assert len(non_executable) == 0, f"Non-executable hooks: {non_executable}"
