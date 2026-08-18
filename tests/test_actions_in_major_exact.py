"""Exact tag pins advance within their major when no floating vN exists."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import scan_actions


def _make_tags_proc(*tags: str) -> MagicMock:
    return MagicMock(returncode=0, stdout="\n".join(tags) + "\n", stderr="")


def _write_workflow(tmp_path: Path, ref: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        f"jobs:\n  ci:\n    steps:\n      - uses: astral-sh/setup-uv@{ref}\n",
        encoding="utf-8",
    )
    return wf


def _scan(tmp_path: Path, tags_proc: MagicMock, **kwargs):
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        return scan_actions(tmp_path, **kwargs)


def test_exact_pin_advances_within_major_without_floating_tag(tmp_path: Path) -> None:
    """setup-uv case: only vN.M.P tags upstream → v8.1.0 must move to v8.2.0."""
    wf = _write_workflow(tmp_path, "v8.1.0")
    tags_proc = _make_tags_proc("v8.0.0", "v8.1.0", "v8.2.0")

    bumps, scanned = _scan(tmp_path, tags_proc, apply=True)

    assert scanned == 1
    rows = [b for b in bumps if b.action == "astral-sh/setup-uv"]
    assert len(rows) == 1
    assert rows[0].pin == "v8.1.0"
    assert rows[0].latest == "v8.2.0"
    assert "report only" not in (rows[0].detail or "")
    assert "astral-sh/setup-uv@v8.2.0" in wf.read_text(encoding="utf-8")


def test_exact_pin_already_highest_stays_report_only(tmp_path: Path) -> None:
    """Highest exact in major and no floating tag → report-only, file untouched."""
    wf = _write_workflow(tmp_path, "v8.2.0")
    tags_proc = _make_tags_proc("v8.0.0", "v8.1.0", "v8.2.0")

    bumps, _ = _scan(tmp_path, tags_proc, apply=True)

    rows = [b for b in bumps if b.action == "astral-sh/setup-uv"]
    assert len(rows) == 1
    assert rows[0].latest == rows[0].pin
    assert "report only" in (rows[0].detail or "")
    assert "astral-sh/setup-uv@v8.2.0" in wf.read_text(encoding="utf-8")
