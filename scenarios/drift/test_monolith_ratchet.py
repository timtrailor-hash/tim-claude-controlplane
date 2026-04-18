"""Size ratchet for conversation_server.py — Phase 3 decomposition forcing function.

The 7,250-line monolith (audit 2026-04-11) is too large to audit, review, or
reason about. The /debate unanimously flagged this as tech debt.

This test enforces a one-way ratchet:
  - The pinned baseline is captured in .conv_server_baseline.
  - Every commit that touches conversation_server.py must leave it the same
    size or smaller than the baseline — never larger.
  - When lines are extracted into conv/*.py, the test author updates the
    baseline to reflect the new reduced target. The baseline can only go
    DOWN.

This converts the monolith from an unbounded liability to a bounded one with
a visible, testable path to zero growth.

Why a test not a hook: the size target needs to be versioned in git,
reviewable in PRs, and visible to Tim in verify.sh output.
"""
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]
SERVER = Path.home() / "code" / "claude-mobile" / "conversation_server.py"
BASELINE_FILE = REPO / ".conv_server_baseline"

# Target: <200 lines (per claude-mobile/CLAUDE.md: "entry point should be <200 lines").
# This is the end state. Baseline tracks current progress toward it.
DECOMPOSITION_TARGET_LINES = 200


@pytest.fixture(scope="module")
def current_lines() -> int:
    if not SERVER.exists():
        pytest.skip(f"{SERVER} not found on this host")
    return sum(1 for _ in SERVER.open())


@pytest.fixture(scope="module")
def baseline_lines() -> int:
    if not BASELINE_FILE.exists():
        pytest.fail(
            f"Baseline missing: {BASELINE_FILE}. "
            f"Create it with: echo <current_line_count> > {BASELINE_FILE}"
        )
    try:
        return int(BASELINE_FILE.read_text().strip())
    except ValueError as e:
        pytest.fail(f"Baseline file unparseable: {e}")


def test_conversation_server_did_not_grow(current_lines, baseline_lines):
    """The monolith must never grow. Shrinking is always fine."""
    assert current_lines <= baseline_lines, (
        f"conversation_server.py grew from {baseline_lines} to {current_lines} lines. "
        f"Phase 3 decomposition requires the monolith to only shrink. "
        f"If you added a feature, extract equal or more lines into conv/*.py first. "
        f"If the addition is truly necessary and cannot be counterbalanced, "
        f"justify in commit msg and update {BASELINE_FILE.name}."
    )


def test_baseline_moves_toward_target(baseline_lines):
    """Baseline should monotonically decrease. Anchors the decomposition trajectory."""
    assert baseline_lines > DECOMPOSITION_TARGET_LINES, (
        f"Baseline {baseline_lines} already ≤ target {DECOMPOSITION_TARGET_LINES}. "
        "Decomposition is complete or this check is stale — remove it."
    )
    # Sanity upper bound: if someone set the baseline far above current reality,
    # the ratchet is ineffective. Cap at 2x the 2026-04-18 snapshot to prevent
    # anti-patterns.
    assert baseline_lines < 15_000, (
        f"Baseline {baseline_lines} is implausibly high. "
        "Did someone set it to `wc -l` of something unrelated?"
    )
