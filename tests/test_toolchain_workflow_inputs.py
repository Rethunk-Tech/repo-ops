"""Versions pinned in a workflow move: step inputs (``go-version:``, ``python-version:``,
``uv-version:``) are bumped, and repo-ops' own CI matrix covers the Python its pin names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_ops_core.holds import clear_hold_caches
from repo_ops_models import Workspace
from repo_ops_upgrade import toolchains

GO_JSON = json.dumps(
    [
        {"version": "go1.27.3", "stable": True},
        {"version": "go1.27.4", "stable": True},
        {"version": "go1.28.0", "stable": True},
    ]
)
PY_JSON = json.dumps([{"latest": "3.14.2"}, {"latest": "3.14.11"}, {"latest": "3.15.0"}])
NODE_JSON = json.dumps([{"version": "v24.9.0"}, {"version": "v24.11.1"}, {"version": "v25.1.0"}])
UV_VERSION = "0.13.9"


@pytest.fixture(autouse=True)
def _feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    feeds = {
        "https://go.dev/dl/?mode=json": GO_JSON,
        "https://endoflife.date/api/python.json": PY_JSON,
        "https://nodejs.org/dist/index.json": NODE_JSON,
    }
    monkeypatch.setattr(toolchains, "_fetch", feeds.get)
    monkeypatch.setattr(toolchains, "_local_go_version", lambda: None)
    monkeypatch.setattr(toolchains, "pypi_latest", lambda _pkg: UV_VERSION)
    clear_hold_caches()


def _ws(path: Path, ecosystem: str, manager: str) -> Workspace:
    return Workspace(path=path, ecosystem=ecosystem, manager=manager, git_repo=path, label="x")


def _workflow(path: Path, body: str) -> Path:
    wf = path / ".github" / "workflows" / "release.yml"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(body)
    return wf


def test_setup_uv_version_input_moves_through_pypi(tmp_path: Path) -> None:
    """``astral-sh/setup-uv`` accepts ``version:``; its sibling key elsewhere does not."""
    wf = _workflow(
        tmp_path,
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - uses: astral-sh/setup-uv@abc123 # v7\n"
        "        with:\n"
        '          version: "0.12.23"\n'
        "      - uses: actions/setup-python@abc123 # v6\n"
        "        with:\n"
        '          version: "3.14.2"\n',
    )

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "py", "uv"))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [("uv-version", "0.12.23", UV_VERSION)]
    assert 'version: "0.13.9"' in wf.read_text()
    # setup-python's ``version:`` is not a pin this understands; python-version is.
    assert 'version: "3.14.2"' in wf.read_text()


def test_go_version_input_advances_within_its_minor(tmp_path: Path) -> None:
    wf = _workflow(
        tmp_path,
        "steps:\n"
        "  - uses: Rethunk-Tech/gh-actions/setup-go@abc123\n"
        "    with:\n"
        '      go-version: "1.27.3"\n',
    )

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "go", "go"))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [("go-version", "1.27.3", "1.27.4")]
    assert 'go-version: "1.27.4"' in wf.read_text()


def test_go_version_stable_alias_takes_the_latest_release(tmp_path: Path) -> None:
    wf = _workflow(tmp_path, "steps:\n  - with:\n      go-version: stable\n")

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "go", "go"))

    assert [(c.from_ver, c.to) for c in changes] == [("stable", "1.28.0")]
    assert "go-version: 1.28.0\n" in wf.read_text()


def test_python_input_stays_in_its_minor(tmp_path: Path) -> None:
    wf = _workflow(
        tmp_path,
        "steps:\n"
        "  - uses: Rethunk-Tech/gh-actions/setup-python@abc123\n"
        "    with:\n"
        '      python-version: "3.14.2"\n',
    )

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "py", "uv"))

    assert [(c.name, c.to) for c in changes] == [("python-version", "3.14.11")]
    assert 'python-version: "3.14.11"' in wf.read_text()


def test_node_input_moves_minor_and_patch_within_its_major(tmp_path: Path) -> None:
    """``24.9`` keeps two-part precision, so it moves to 24.11 and not to 25.1."""
    wf = _workflow(
        tmp_path,
        "steps:\n"
        "  - uses: Rethunk-Tech/gh-actions/setup-bun@abc123\n"
        "    with:\n"
        '      node-version: "24.9"\n',
    )

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "node", "bun"))

    assert [(c.name, c.to) for c in changes] == [("node-version", "24.11")]
    assert 'node-version: "24.11"' in wf.read_text()


def test_expression_matrix_and_latest_inputs_are_left_alone(tmp_path: Path) -> None:
    body = (
        "jobs:\n"
        "  build:\n"
        "    strategy:\n"
        "      matrix:\n"
        '        python-version: ["3.12", "3.13"]\n'
        "    steps:\n"
        "      - uses: astral-sh/setup-uv@abc123\n"
        "        with:\n"
        "          version: latest\n"
        "      - with:\n"
        '          python-version: "${{ matrix.python-version }}"\n'
    )
    wf = _workflow(tmp_path, body)

    assert toolchains.apply_toolchains(_ws(tmp_path, "py", "uv")) == ([], [])
    assert wf.read_text() == body


def test_a_hold_caps_a_workflow_input_too(tmp_path: Path) -> None:
    (tmp_path / ".repo-ops.toml").write_text(
        '[[hold]]\npackage = "python"\nmax_version = "3.14.5"\n'
    )
    wf = _workflow(
        tmp_path,
        'steps:\n  - with:\n      python-version: "3.14.2"\n',
    )

    changes, behind = toolchains.apply_toolchains(_ws(tmp_path, "py", "uv"))

    assert [c.name for c in changes] == []
    assert [(b.name, b.from_ver, b.to) for b in behind] == [("python-version", "3.14.2", "3.14.11")]
    assert "3.14.2" in wf.read_text()


def test_own_ci_matrix_covers_the_python_the_pin_names() -> None:
    """This repo's ``.python-version`` and its CI matrix must not disagree."""
    repo = Path(__file__).resolve().parent.parent
    pin = (repo / ".python-version").read_text().strip()
    workflow = (repo / ".github" / "workflows" / "ci.yml").read_text()
    assert f'"{pin}"' in workflow, f"ci.yml matrix is missing the {pin} pin from .python-version"
