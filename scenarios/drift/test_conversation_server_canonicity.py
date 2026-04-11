"""
conversation_server.py canonicity test.

Audit 2026-04-11 §3.3: conversation_server.py had three divergent copies across
two hosts. This test enforces that exactly ONE runtime-reachable copy exists,
and only on Tims-Mac-mini.local at ~/code/claude-mobile/conversation_server.py.

Runtime-reachable = inside a directory listed in the daemon's PYTHONPATH or
inside ~/code/. User-managed iCloud folders, audit snapshots, and quarantine
dirs are explicitly excluded — they cannot accidentally be run as the daemon.
"""
import os
import socket
import subprocess
from pathlib import Path

import pytest

CANONICAL_HOST = "Tims-Mac-mini.local"
CANONICAL_PATH = Path.home() / "code" / "claude-mobile" / "conversation_server.py"

# Paths that may contain conversation_server.py without counting toward drift.
EXCLUDED_PATH_SUBSTRINGS = [
    "_quarantine",                 # deliberate quarantine dirs
    "audit_2026",                   # audit evidence snapshots
    "__pycache__",                  # compiled bytecode
    "Documents - Tim",              # iCloud shared folder (user-managed, not runtime)
    ".bak",                         # historical backups
]


def _is_excluded(path: str) -> bool:
    return any(s in path for s in EXCLUDED_PATH_SUBSTRINGS)


def _find_runtime_copies():
    """Find every conversation_server.py on disk that isn't in an excluded path."""
    roots = [Path.home() / "code", Path.home() / "Documents"]
    found = []
    for root in roots:
        if not root.exists():
            continue
        try:
            result = subprocess.run(
                ["find", str(root), "-name", "conversation_server.py"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or _is_excluded(line):
                continue
            found.append(line)
    return sorted(set(found))


class TestConversationServerCanonicity:
    def test_canonical_file_exists_on_mac_mini(self):
        """On the Mac Mini, ~/code/claude-mobile/conversation_server.py MUST exist."""
        hostname = socket.gethostname()
        if not hostname.startswith("Tims-Mac-mini"):
            pytest.skip(f"Not the Mac Mini (host={hostname})")
        assert CANONICAL_PATH.exists(), \
            f"Canonical conversation_server.py missing at {CANONICAL_PATH}"
        size = CANONICAL_PATH.stat().st_size
        assert size > 200_000, \
            f"Canonical file is suspiciously small ({size} bytes) — possible rollback?"

    def test_no_runtime_duplicates_exist(self):
        """Exactly zero runtime-reachable conversation_server.py copies outside canonical."""
        hostname = socket.gethostname()
        found = _find_runtime_copies()

        if hostname.startswith("Tims-Mac-mini"):
            expected = {str(CANONICAL_PATH)}
        else:
            # MacBook Pro and any other host: zero copies expected.
            expected = set()

        extras = set(found) - expected
        assert not extras, (
            f"Runtime drift detected on {hostname}: "
            f"unexpected conversation_server.py at: {sorted(extras)}"
        )

    def test_no_bak_file_on_mac_mini(self):
        """The .bak from audit §3.3 must be deleted."""
        hostname = socket.gethostname()
        if not hostname.startswith("Tims-Mac-mini"):
            pytest.skip(f"Not the Mac Mini (host={hostname})")
        bak = CANONICAL_PATH.with_suffix(".py.bak")
        assert not bak.exists(), f"Stale .bak still present at {bak}"

    def test_daemon_wrapper_has_cd_guard(self):
        """conversation_daemon.sh must fail-fast on cd failure (audit §4.3)."""
        wrapper = Path.home() / ".local" / "bin" / "conversation_daemon.sh"
        if not wrapper.exists():
            pytest.skip("conversation_daemon.sh not present on this host")
        content = wrapper.read_text()
        assert "cd " in content and "|| " in content and "exit 1" in content, (
            f"conversation_daemon.sh lacks `cd ... || exit 1` guard — "
            f"silent cd failure pattern. See audit §4.3."
        )
