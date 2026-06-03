"""Tests for SHA-pin resolution (issue #21): unpin SHA-pinned Actions to version tags."""

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
# (a) Comment-driven resolution: "# v6" → @v6
# ---------------------------------------------------------------------------


def test_comment_hint_sha_resolved_major(tmp_path: Path) -> None:
    """SHA pin with # v6 comment → @v6; comment dropped on apply."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}  # v6\n",
    )
    # Name-only lines (backwards compat) — no SHA resolution needed, comment drives it.
    tags_proc = _make_tags_proc("v6", "v6.0.1", "v5", "v5.1.0")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(tmp_path, apply=True)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1, bumps
    b = checkout[0]
    assert b.pin == _SHA
    assert b.latest == "v6"
    assert "unpin SHA to v6" in (b.detail or "")
    assert "report only" not in (b.detail or "").lower()

    # File should be rewritten to @v6 without the trailing comment
    text = wf.read_text(encoding="utf-8")
    assert "actions/checkout@v6" in text
    assert _SHA not in text
    # Comment should be dropped
    assert "# v6" not in text


# ---------------------------------------------------------------------------
# (b) API-driven SHA→tag resolution (via sha_to_tags mock)
# ---------------------------------------------------------------------------


def test_api_sha_resolution_major(tmp_path: Path) -> None:
    """SHA with no comment; tags API maps SHA to v4.2.1 → resolved to @v4."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}\n",
    )
    # Lines with "name sha" format — SHA maps to v4.2.1
    tags_proc = _make_tags_proc(
        f"v4 {_SHA2}",  # v4 bare points elsewhere
        f"v4.2.1 {_SHA}",  # this SHA is v4.2.1
        f"v4.2.0 {_SHA2}",
        f"v3 {_SHA2}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(tmp_path, apply=False)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.pin == _SHA
    assert b.latest == "v4"
    assert "unpin SHA to v4" in (b.detail or "")
    assert "report only" not in (b.detail or "").lower()

    # Plan must not write
    text = wf.read_text(encoding="utf-8")
    assert _SHA in text


def test_api_sha_resolution_applies_rewrite(tmp_path: Path) -> None:
    """apply=True rewrites SHA to @v4 when API resolves it."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}\n",
    )
    tags_proc = _make_tags_proc(
        f"v4.2.1 {_SHA}",
        f"v4 {_SHA2}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].latest == "v4"
    text = wf.read_text(encoding="utf-8")
    assert "actions/checkout@v4" in text
    assert _SHA not in text


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


def test_unresolvable_sha_gh_absent(tmp_path: Path) -> None:
    """When gh is absent, SHA pin is reported as unresolvable (keep_sha=False path)."""
    _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}\n",
    )
    with patch("repo_ops_upgrade.actions.shutil.which", return_value=None):
        bumps, _ = scan_actions(tmp_path, apply=False)

    # gh absent → action_tag_info returns None → unresolvable path
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.latest == _SHA
    assert "report only" in (b.detail or "").lower()


# ---------------------------------------------------------------------------
# (d) --actions-tag-style=exact → @vN.N.N
# ---------------------------------------------------------------------------


def test_exact_tag_style(tmp_path: Path) -> None:
    """tag_style='exact' → highest vN.M.P within resolved major."""
    _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}  # v4\n",
    )
    # Provide multiple v4.x.y tags so we can confirm the highest is chosen
    tags_proc = _make_tags_proc(
        f"v4 {_SHA2}",
        f"v4.2.1 {_SHA2}",
        f"v4.3.0 {_SHA2}",
        f"v4.1.0 {_SHA2}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=False, tag_style="exact")

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.latest == "v4.3.0"
    assert "report only" not in (b.detail or "").lower()


def test_exact_tag_style_fallback_to_major(tmp_path: Path) -> None:
    """tag_style='exact' falls back to @vN when no vN.M.P tags exist for that major."""
    _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}  # v4\n",
    )
    # Only a bare v4 tag, no exact tags for v4
    tags_proc = _make_tags_proc(f"v4 {_SHA2}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=False, tag_style="exact")

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.latest == "v4"
    assert "major (no exact tag found)" in (b.detail or "")


# ---------------------------------------------------------------------------
# (e) --actions-keep-sha → no-op (preserves old report-only behavior)
# ---------------------------------------------------------------------------


def test_keep_sha_noop(tmp_path: Path) -> None:
    """keep_sha=True: SHA pin reported as-is; no rewrite on apply."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA}  # v4\n",
    )
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(f"v4 {_SHA}", f"v4.2.1 {_SHA}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.pin == _SHA
    assert b.latest == _SHA
    assert b.detail == "SHA-pinned; report only"
    # File must be unchanged
    assert wf.read_text(encoding="utf-8") == original
