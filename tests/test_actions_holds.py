"""GitHub Actions wiring for package version-ceiling ("hold") rules."""

import re
from pathlib import Path

import pytest

from repo_ops_core.holds import clear_hold_caches

_ACTION = "actions/checkout"


def _workflow(tmp_path, pin: str):
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: CI\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - uses: {_ACTION}@{pin}\n",
        encoding="utf-8",
    )
    return workflow


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


def _write_global_hold(tmp_path: Path, **overrides) -> None:
    defaults = {"package": _ACTION, "max_version": "4.3", "ecosystem": "actions"}
    defaults.update(overrides)
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir(exist_ok=True)
    lines = ["[[hold]]", *(f'{key} = "{value}"' for key, value in defaults.items())]
    (cfg_dir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tag_info(tags: list[str], sha_to_tags: dict[str, list[str]] | None = None) -> dict:
    majors = {
        int(match.group(1)) for tag in tags if (match := re.fullmatch(r"v(\d+)", tag)) is not None
    }
    all_majors = {
        int(match.group(1)) for tag in tags if (match := re.match(r"^v(\d+)", tag)) is not None
    }
    return {
        "bare_majors": majors,
        "max_major": max(all_majors) if all_majors else None,
        "all_tags": set(tags),
        "sha_to_tags": sha_to_tags or {},
    }
