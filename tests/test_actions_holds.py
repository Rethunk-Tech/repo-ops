"""GitHub Actions wiring for package version-ceiling ("hold") rules."""

from pathlib import Path

import pytest

from repo_ops_core.holds import clear_hold_caches


@pytest.fixture(autouse=True)
def _isolated_hold_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the global hold config at a tmp dir and drop the process cache.

    ``_global_holds_cached``/``_repo_holds_cached`` are ``@cache``d for the
    process lifetime, so without this every test after the first would keep
    reusing whatever the first one loaded.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    clear_hold_caches()
    yield
    clear_hold_caches()
