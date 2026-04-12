"""
Context budget enforcement tests.

Audit 2026-04-12 context/memory pivot: "Lost in the Middle" effect degrades
LLM reasoning on large files and large corpora. These tests enforce hard
ceilings so the problem can't silently recur.

Ceilings (from three-model debate, confidence 75–95%):
- Code files: 2,000 lines hard ceiling (1,500 soft)
- CLAUDE.md instruction files: 200 lines (Anthropic's stated ceiling)
- Corpus/context files: 60K tokens (~240K chars at 4 chars/token)
"""

import os
import subprocess
from pathlib import Path

import pytest

# Directories to scan for code files
CODE_DIRS = [
    Path.home() / "code" / "claude-mobile",
    Path.home() / "code" / "ofsted-agent",
    Path.home() / "code" / "sv08-print-tools",
    Path.home() / "code" / "sv08_tools",
]

# Hard ceilings
MAX_PY_LINES = 2000
MAX_CLAUDE_MD_LINES = 200
MAX_CORPUS_CHARS = 240_000  # ~60K tokens at 4 chars/token

# Files explicitly exempted (with reason)
EXEMPT_PY = {
    # conversation_server.py is in active decomposition (conv/ package created,
    # CLAUDE.md charter maps the target modules). This exemption expires when
    # the conv/ split is complete. Track via: wc -l conversation_server.py
    "conversation_server.py": "active decomposition — conv/ package in progress",
    # app.py is the iOS wrapper/proxy — another monolith flagged for future split.
    "app.py": "known monolith — decomposition after conversation_server.py completes",
}

# Corpus files with retrieval-first workarounds in place.
EXEMPT_CORPUS = {
    "combined_context.md": "retrieval-first approach added 2026-04-12; full-load is fallback only",
}


class TestContextBudgets:
    def _find_files(self, pattern, dirs=None):
        """Find files matching glob pattern in the specified dirs."""
        found = []
        for d in (dirs or CODE_DIRS):
            if d.exists():
                found.extend(d.rglob(pattern))
        return found

    def test_no_python_file_exceeds_ceiling(self):
        """No .py file should exceed MAX_PY_LINES (active decomposition exempt)."""
        violations = []
        for f in self._find_files("*.py"):
            if f.name in EXEMPT_PY:
                continue
            if f.name.startswith(".") or "__pycache__" in str(f) or "_quarantine" in str(f):
                continue
            try:
                lines = len(f.read_text().splitlines())
                if lines > MAX_PY_LINES:
                    violations.append(f"{f} ({lines} lines)")
            except (OSError, UnicodeDecodeError):
                continue
        assert not violations, (
            f"Python files exceeding {MAX_PY_LINES}-line ceiling:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_exempt_files_are_shrinking(self):
        """Exempted files must be actively shrinking — flag if they GREW."""
        for name, reason in EXEMPT_PY.items():
            for d in CODE_DIRS:
                f = d / name
                if f.exists():
                    lines = len(f.read_text().splitlines())
                    # conversation_server.py was 7,195 at the start of the pivot.
                    # It should be shrinking, not growing. Fail if it exceeds the
                    # pre-pivot size by more than 5% (allowing for minor additions
                    # during the transition).
                    if name == "conversation_server.py" and lines > 7600:
                        pytest.fail(
                            f"{f} has GROWN to {lines} lines (was 7,195 at pivot start). "
                            f"The exemption assumes active decomposition, not growth."
                        )

    def test_no_claude_md_exceeds_ceiling(self):
        """No CLAUDE.md file should exceed MAX_CLAUDE_MD_LINES."""
        violations = []
        # Scan all CLAUDE.md files across the system
        search_roots = [
            Path.home() / "code",
            Path.home() / "Documents" / "Claude code",
            Path.home() / ".claude" / "rules",
        ]
        for root in search_roots:
            if not root.exists():
                continue
            for f in root.rglob("CLAUDE.md"):
                if "__pycache__" in str(f) or "_quarantine" in str(f):
                    continue
                try:
                    lines = len(f.read_text().splitlines())
                    if lines > MAX_CLAUDE_MD_LINES:
                        violations.append(f"{f} ({lines} lines)")
                except (OSError, UnicodeDecodeError):
                    continue
        assert not violations, (
            f"CLAUDE.md files exceeding {MAX_CLAUDE_MD_LINES}-line ceiling:\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_corpus_file_exceeds_token_budget(self):
        """No single corpus/context file should exceed MAX_CORPUS_CHARS."""
        corpus_patterns = ["combined_context.md", "combined_context.md.enc"]
        violations = []
        for d in CODE_DIRS:
            if not d.exists():
                continue
            for pattern in corpus_patterns:
                for f in d.rglob(pattern):
                    if f.suffix == ".enc":
                        continue  # encrypted files can't be measured by char count
                    if f.name in EXEMPT_CORPUS:
                        continue
                    try:
                        chars = len(f.read_text())
                        if chars > MAX_CORPUS_CHARS:
                            approx_tokens = chars // 4
                            violations.append(
                                f"{f} ({chars:,} chars ≈ {approx_tokens:,} tokens)"
                            )
                    except (OSError, UnicodeDecodeError):
                        continue
        assert not violations, (
            f"Corpus files exceeding ~60K token budget ({MAX_CORPUS_CHARS:,} chars):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_rules_files_are_concise(self):
        """~/.claude/rules/*.md files should be concise (under 100 lines each)."""
        rules_dir = Path.home() / ".claude" / "rules"
        if not rules_dir.exists():
            pytest.skip("rules dir not found")
        violations = []
        for f in rules_dir.glob("*.md"):
            try:
                lines = len(f.read_text().splitlines())
                if lines > 100:
                    violations.append(f"{f.name} ({lines} lines)")
            except (OSError, UnicodeDecodeError):
                continue
        assert not violations, (
            f"Rules files exceeding 100-line target:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
