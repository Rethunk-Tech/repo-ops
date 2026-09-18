"""SHA-pinned Actions that cannot be refreshed stay report-only."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import scan_actions

_SHA = "abc1234def5678901234567890abc1234def5678"
_SHA2 = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _make_tags_proc(*lines: str) -> MagicMock:
    """Build a mock process returning newline-joined lines (name sha or name-only)."""
    return MagicMock(returncode=0, stdout="\n".join(lines) + "\n", stderr="")


def _wf(tmp_path: Path, content: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(content, encoding="utf-8")
    return wf


# ---------------------------------------------------------------------------
# (c) Unresolvable SHA: left untouched + still reported
# ---------------------------------------------------------------------------


def test_unresolvable_sha_report_only(tmp_path: Path) -> None:
    """SHA with no comment and no tag mapping → reported but file unchanged."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}\n",
    )
    # Tags exist but none map to our SHA
    tags_proc = _make_tags_proc(
        f"v4 {_SHA2}",
        f"v4.2.1 {_SHA2}",
    )
    original = wf.read_text(encoding="utf-8")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.pin == _SHA
    assert b.latest == _SHA  # unchanged
    assert "report only" in (b.detail or "").lower()
    # File must be unchanged
    assert wf.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# (e) (the default) → SHA pins are report-only
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# (g) short-SHA prefix resolution
# ---------------------------------------------------------------------------

# Full 40-char SHA used as the key in sha_to_tags (as the real API returns).
_FULL_SHA_A = "abc1234def5678901234567890abc1234def5678"
# 7-char prefix of _FULL_SHA_A

# A second full SHA whose first 7 chars differ from _FULL_SHA_A
