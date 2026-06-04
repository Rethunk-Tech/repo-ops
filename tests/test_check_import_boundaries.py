"""Regression tests for scripts/check_import_boundaries.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_import_boundaries.py"
_spec = importlib.util.spec_from_file_location("check_import_boundaries", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_import_boundaries = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_import_boundaries)


def test_dotless_import_from_repo_ops_upgrade_disallowed_and_allowed(
    tmp_path: Path,
) -> None:
    """Dotless `from repo_ops_upgrade import X` must be subject to the allowlist.

    ``orchestration`` is not in ALLOWED_UPGRADE_SUBMODULES → upgrade_subs should
    contain it so the disallowed-submodule check fires.
    ``registry`` IS in ALLOWED_UPGRADE_SUBMODULES → upgrade_subs - allowed == empty.
    """
    bad = tmp_path / "bad.py"
    bad.write_text("from repo_ops_upgrade import orchestration\n", encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text("from repo_ops_upgrade import registry\n", encoding="utf-8")

    _, bad_subs = check_import_boundaries._imports_in_file(bad)
    _, good_subs = check_import_boundaries._imports_in_file(good)

    allowed = check_import_boundaries.ALLOWED_UPGRADE_SUBMODULES
    # "orchestration" must appear and be disallowed
    assert "orchestration" in bad_subs
    assert bad_subs - allowed == {"orchestration"}
    # "registry" must appear and be allowed
    assert "registry" in good_subs
    assert good_subs - allowed == set()
