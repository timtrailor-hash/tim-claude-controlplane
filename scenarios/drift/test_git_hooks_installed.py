"""Meta-verification: git hooks must remain installed across all managed repos.

Tonight's fresh-agent finding proved that gates you must remember to run
aren't durable. The fix was to install pre-commit + pre-push hooks
automatically. THIS test guards that they STAY installed — if a `git clone`
of a fresh copy bypasses the hooks, this test fails on the next deploy.

Pattern being prevented: the 2026-04-18 regression where two bug fixes
were committed without running verify.sh, silently breaking the ratchet.
If pre-commit had been installed, those commits would have been blocked.
"""
from pathlib import Path
import subprocess
import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "shared" / "git-hooks" / "install.sh"


def test_hook_installer_exists():
    assert INSTALLER.exists(), f"missing {INSTALLER}"
    assert INSTALLER.stat().st_mode & 0o111, f"{INSTALLER} not executable"


def test_hook_sources_exist():
    for hook in ("pre-commit", "pre-push", "post-commit"):
        p = REPO / "shared" / "git-hooks" / hook
        assert p.exists(), f"missing {p}"
        assert p.stat().st_mode & 0o111, f"{p} not executable"


def test_hooks_installed_in_managed_repos():
    """Run installer in --verify mode. Must return 0 = all symlinked."""
    r = subprocess.run(
        ["bash", str(INSTALLER), "--verify"],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        pytest.fail(
            f"git hooks not installed everywhere (rc={r.returncode}):\n"
            f"{r.stdout}\n{r.stderr}\n"
            f"Fix: bash {INSTALLER}"
        )


def test_pre_commit_enforces_ratchet():
    """Sanity: pre-commit script enforces a ceiling on conversation_server.py."""
    p = REPO / "shared" / "git-hooks" / "pre-commit"
    text = p.read_text()
    assert "conversation_server.py" in text, "pre-commit no longer watches the monolith"
    # Replaced strict shrink-only ratchet with a ceiling 2026-04-28.
    # The hook used to reject growth past .conv_server_baseline; it now rejects
    # growth past CEILING (currently 7000).
    assert "CEILING" in text or "ceiling" in text, (
        "pre-commit no longer enforces a monolith ceiling"
    )


def test_pre_push_runs_verify():
    """Sanity: pre-push script invokes verify.sh."""
    p = REPO / "shared" / "git-hooks" / "pre-push"
    text = p.read_text()
    assert "verify.sh" in text, "pre-push no longer runs verify.sh"
    assert "refs/heads/main" in text, "pre-push no longer gates main branch"
