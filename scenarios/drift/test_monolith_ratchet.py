"""Size ceiling for conversation_server.py — replaced strict ratchet 2026-04-28.

Background: Phase 3 decomposition (slices 1c–1L) moved 1,700 lines out of
conversation_server.py and into conv/<slice>/ packages. The strict
"monotonically decrease" ratchet was a useful forcing function during the
active extraction. In maintenance mode it became net cost — fired on every
minor hotfix that added even a few comment lines (false alerts 2026-04-27
and 2026-04-28 mornings).

New policy (matches claude-mobile/.github/workflows/ratchet.yml and
claude-mobile/shared/git-hooks/pre-commit): alert only if the monolith
exceeds CEILING lines. The current snapshot is 5,663; 7,000 gives ~1,300
lines of headroom for normal hotfixes. The ceiling fires only if someone
adds a whole new feature to the monolith instead of extracting it into a
conv/<slice>/ package — which is the actual behaviour worth alerting on,
not "added 13 explanatory comment lines in a safety patch".
"""
from pathlib import Path
import pytest

SERVER = Path.home() / "code" / "claude-mobile" / "conversation_server.py"
CEILING = 7000


@pytest.fixture(scope="module")
def current_lines() -> int:
    if not SERVER.exists():
        pytest.skip(f"{SERVER} not found on this host")
    return sum(1 for _ in SERVER.open())


def test_conversation_server_under_ceiling(current_lines):
    """The monolith must stay under CEILING lines.

    If this fires, someone added a chunk of code to the monolith instead of
    extracting it into a conv/<slice>/ package. Either extract, or — if the
    addition is genuinely core (subprocess lifecycle, session state,
    permission bridge expansion) — raise the ceiling here, in
    .github/workflows/ratchet.yml, and in shared/git-hooks/pre-commit, with
    a justifying commit message.
    """
    assert current_lines <= CEILING, (
        f"conversation_server.py = {current_lines} lines, exceeds ceiling {CEILING}. "
        f"Either extract code into a conv/<slice>/ package, or raise the ceiling "
        f"with a justifying commit message."
    )


def test_ceiling_is_realistic(current_lines):
    """Sanity: ceiling should be above current size with reasonable headroom but
    not so far above that the alert is meaningless. If the gap closes, plan a
    decomposition push or accept the new normal explicitly."""
    headroom = CEILING - current_lines
    assert headroom > 0, (
        f"Ceiling {CEILING} is below current {current_lines} — already broken."
    )
    assert headroom < 3000, (
        f"Ceiling {CEILING} is more than 3000 above current {current_lines}. "
        f"Ratchet has lost its signal — tighten the ceiling or remove the test."
    )
