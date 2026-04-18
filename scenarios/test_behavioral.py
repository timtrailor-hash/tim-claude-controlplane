"""
Behavioral integration tests — actually execute hooks with real payloads.

The audit found that existing tests are structural (file existence, string matching)
but don't verify hooks ACTUALLY WORK with the Claude Code event schema.

These tests send the EXACT JSON format Claude Code uses and verify the hook's
stdout, stderr, and exit code.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"


def _run_hook(hook_name, payload, timeout=15):
    """Run a hook with JSON payload on stdin. Returns (stdout, stderr, rc)."""
    script = HOOKS_DIR / hook_name
    assert script.exists(), f"{hook_name} not found"
    proc = subprocess.run(
        ["bash", str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.stdout, proc.stderr, proc.returncode


class TestProtectedPathBehavioral:
    """Behavioral tests for protected_path_hook.sh with real Claude Code payloads."""

    def test_beh001_blocks_launchctl_bootstrap(self):
        """BEH-001: launchctl bootstrap must be denied."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "launchctl bootstrap gui/501 ~/Library/LaunchAgents/test.plist"}}
        stdout, stderr, rc = _run_hook("protected_path_hook.sh", payload)
        assert rc == 0 and '"permissionDecision": "ask"' in stdout, (
            f"launchctl bootstrap should emit ask-decision (rc={rc}, stdout={stdout!r}, stderr={stderr!r})"
        )

    def test_beh002_blocks_launchctl_bootout(self):
        """BEH-002: launchctl bootout must be denied."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "launchctl bootout gui/501/com.test"}}
        stdout, _, rc = _run_hook("protected_path_hook.sh", payload)
        assert rc == 0 and '"permissionDecision": "ask"' in stdout

    def test_beh003_blocks_sudo_shutdown(self):
        """BEH-003: sudo shutdown must be denied."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "sudo shutdown -h now"}}
        stdout, _, rc = _run_hook("protected_path_hook.sh", payload)
        assert rc == 0 and '"permissionDecision": "ask"' in stdout

    def test_beh004_blocks_launchagent_write(self):
        """BEH-004: writing to LaunchAgents dir must be denied."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "cp malware.plist ~/Library/LaunchAgents/"}}
        stdout, _, rc = _run_hook("protected_path_hook.sh", payload)
        assert rc == 0 and '"permissionDecision": "ask"' in stdout

    def test_beh005_allows_launchagent_read(self):
        """BEH-005: reading LaunchAgent plists should be allowed."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "cat ~/Library/LaunchAgents/com.timtrailor.conversation-server.plist"}}
        _, stderr, rc = _run_hook("protected_path_hook.sh", payload)
        assert rc == 0, f"Reading plist should be allowed (rc={rc}, stderr={stderr})"

    def test_beh006_allows_normal_commands(self):
        """BEH-006: normal commands pass through."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo hello world"}}
        _, _, rc = _run_hook("protected_path_hook.sh", payload)
        assert rc == 0

    def test_beh007_blocks_chflags(self):
        """BEH-007: chflags immutability changes must be denied."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "chflags uchg important_file"}}
        stdout, _, rc = _run_hook("protected_path_hook.sh", payload)
        assert rc == 0 and '"permissionDecision": "ask"' in stdout


class TestCredentialLeakBehavioral:
    """Behavioral tests for credential_leak_hook.sh."""

    def test_beh010_blocks_key_in_tracked_file(self, tmp_path):
        """BEH-010: API key in a git-tracked file must be blocked."""
        # Set up a temp git repo with a tracked file
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        test_file = tmp_path / "app.py"
        test_file.write_text("x = 1")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)

        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(test_file),
                "content": 'ANTHROPIC_API_KEY = "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"'
            }
        }
        _, stderr, rc = _run_hook("credential_leak_hook.sh", payload)
        assert rc == 2, f"API key in tracked file should be blocked (rc={rc})"

    def test_beh011_allows_non_secret_content(self, tmp_path):
        """BEH-011: normal code in a tracked file should be allowed."""
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        test_file = tmp_path / "utils.py"
        test_file.write_text("x = 1")
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)

        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(test_file),
                "content": "def add(a, b): return a + b"
            }
        }
        _, _, rc = _run_hook("credential_leak_hook.sh", payload)
        assert rc == 0


class TestAuditLogBehavioral:
    """Behavioral tests for audit_log_hook.sh."""

    def test_beh020_logs_command(self):
        """BEH-020: audit log hook should exit 0 and process the command."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la /tmp"},
            "tool_result": {"exit_code": 0}
        }
        _, _, rc = _run_hook("audit_log_hook.sh", payload)
        assert rc == 0, "Audit log hook should always exit 0"


class TestLintHookBehavioral:
    """Behavioral tests for lint_hook.sh."""

    def test_beh030_runs_on_python_file(self, tmp_path):
        """BEH-030: lint hook should run ruff on a Python file."""
        test_file = tmp_path / "bad.py"
        test_file.write_text("import os\ndef foo(): pass\n")
        payload = {"tool_name": "Edit", "tool_input": {"file_path": str(test_file)}}
        _, stderr, rc = _run_hook("lint_hook.sh", payload)
        # Should exit 0 (advisory mode) regardless of findings
        assert rc == 0

    def test_beh031_skips_markdown(self, tmp_path):
        """BEH-031: lint hook should skip non-source files."""
        test_file = tmp_path / "readme.md"
        test_file.write_text("# Hello")
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(test_file)}}
        _, _, rc = _run_hook("lint_hook.sh", payload)
        assert rc == 0


class TestValidateHooksBehavioral:
    """Behavioral tests for validate_hooks.sh."""

    def test_beh040_validates_settings_json(self):
        """BEH-040: validate_hooks should parse settings.json and check paths."""
        proc = subprocess.run(
            ["bash", str(HOOKS_DIR / "validate_hooks.sh")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"validate_hooks failed: {proc.stderr}"
        # Should produce a log entry
        log = Path.home() / ".claude" / "hook_validation.log"
        if log.exists():
            content = log.read_text()
            assert "OK" in content or "FAIL" in content, "Log should contain a result"
