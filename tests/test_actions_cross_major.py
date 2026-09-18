"""Cross-major GitHub Actions bumps under latest mode."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import scan_actions


def _make_tags_proc(*tags: str) -> MagicMock:
    """Mock ``gh api .../tags`` output: one ``name sha`` line per tag."""
    return MagicMock(
        returncode=0, stdout="".join(f"{t} {t.encode().hex():0<40.40}\n" for t in tags), stderr=""
    )


def _write_workflow(tmp_path: Path, ref: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{ref}\n",
        encoding="utf-8",
    )
    return wf


def _scan(tmp_path: Path, tags_proc: MagicMock, **kwargs):
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        return scan_actions(tmp_path, **kwargs)


# ---------------------------------------------------------------------------
# Cross-major SHA-pin refresh
# Extends coverage for the keep-sha + latest interaction from the cross-major
# perspective (SHA-specific depth is in test_actions_sha_refresh.py).
# ---------------------------------------------------------------------------

_SHA_V4 = "aabbccddeeff00112233445566778899aabbccdd"
_SHA_V6 = "00112233445566778899aabbccddeeff00112233"


def test_sha_pin_cross_major_refresh_under_latest(tmp_path: Path) -> None:
    """SHA-pinned v4 action advances to v6's digest when --latest + --actions-refresh-sha."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA_V4}  # v4\n",
        encoding="utf-8",
    )
    tags_proc = MagicMock(
        returncode=0,
        stdout=(f"v6.0.1 {_SHA_V6}\nv6 {_SHA_V6}\nv4.2.1 {_SHA_V4}\nv4 {_SHA_V4}\n"),
        stderr="",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(
            tmp_path,
            apply=True,
        )

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.pin == _SHA_V4
    assert b.latest == _SHA_V6
    assert "cross-major" in (b.detail or "").lower()

    text = wf.read_text(encoding="utf-8")
    assert _SHA_V6 in text, "v6 digest not written"
    assert _SHA_V4 not in text, "old v4 digest still present"
    assert "# v6" in text, "comment not updated to v6"
    assert "# v4" not in text, "old comment still present"
