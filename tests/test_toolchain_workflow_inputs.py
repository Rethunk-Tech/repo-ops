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
NODE_JSON = json.dumps(
    [
        {"version": "v26.4.0", "lts": "Krypton"},
        {"version": "v24.11.1", "lts": "Jod"},
        {"version": "v25.1.0"},
    ]
)
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
    toolchains.reset_failed_pins()
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


def test_node_input_moves_to_the_newest_lts_major(tmp_path: Path) -> None:
    """A workflow literal follows the newest LTS, full or bare major, never a Current odd major."""
    wf = _workflow(
        tmp_path,
        "steps:\n"
        "  - uses: Rethunk-Tech/gh-actions/setup-bun@abc123\n"
        "    with:\n"
        '      node-version: "24"\n'
        "  - uses: Rethunk-Tech/gh-actions/setup-node@abc123\n"
        "    with:\n"
        '      node-version: "22.9.0"\n',
    )

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "node", "bun"))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [
        ("node-version", "24", "26"),
        ("node-version", "22.9.0", "26.4.0"),
    ]
    text = wf.read_text()
    assert 'node-version: "26.4.0"' in text
    assert 'node-version: "26"' in text


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


_GATE_SHA = "a" * 40
_OLD_SHA = "4fa5cac1effa39318db55c2d0678756c91599957"
_PROXY = "https://proxy.golang.org/github.com/!rethunk-!tech/rethunk-gate-cli"
_GATE = "github.com/Rethunk-Tech/rethunk-gate-cli"


@pytest.fixture
def _gate_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    feeds = {
        f"{_PROXY}/@latest": json.dumps({"Version": "v0.10.3"}),
        f"{_PROXY}/@v/v0.10.3.info": json.dumps({"Origin": {"Hash": _GATE_SHA}}),
        "https://api.github.com/repos/Rethunk-Tech/rethunk-gate-cli/releases/latest": json.dumps(
            {"tag_name": "v0.10.3"}
        ),
    }
    monkeypatch.setattr(toolchains, "_fetch", feeds.get)


@pytest.mark.usefixtures("_gate_feeds")
def test_gate_installs_in_a_workflow_move_to_the_latest_release(tmp_path: Path) -> None:
    wf = _workflow(
        tmp_path,
        "steps:\n"
        f"  - run: GOTOOLCHAIN=auto go install {_GATE}/cmd/gate@v0.10.1\n"
        f"  - run: go install {_GATE}/cmd/gate@{_OLD_SHA} # v0.10.1\n"
        f"  - run: curl -fsSL -o gate https://github.com/Rethunk-Tech/{_GATE.rsplit('/', 1)[1]}"
        "/releases/download/v0.10.1/gate-linux-amd64\n",
    )

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "rust", "cargo"))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [("gate", "0.10.1", "0.10.3")] * 3
    text = wf.read_text()
    assert f"go install {_GATE}/cmd/gate@v0.10.3\n" in text
    assert f"{_GATE}/cmd/gate@{_GATE_SHA} # v0.10.3\n" in text
    assert "releases/download/v0.10.3/gate-linux-amd64" in wf.read_text()


_OLD_DIGEST = "0123456789abcdef" * 4
_NEW_DIGEST = "fedcba9876543210" * 4


def _download_step(version: str = "v0.10.1", digest: str = _OLD_DIGEST) -> str:
    url = (
        "https://github.com/Rethunk-Tech/rethunk-gate-cli/releases/download/"
        f"{version}/gate-linux-amd64"
    )
    return (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - name: Install gate\n"
        "        run: |\n"
        f'          curl -fsSL -o "$RUNNER_TEMP/gate" {url}\n'
        f'          echo "{digest}  $RUNNER_TEMP/gate" | sha256sum -c -\n'
    )


@pytest.mark.usefixtures("_gate_feeds")
def test_url_gate_pin_bump_carries_the_new_assets_sha256(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sha256 verifying the download moves with the URL, or CI fails the checksum."""
    wf = _workflow(tmp_path, _download_step())
    monkeypatch.setattr(
        toolchains,
        "_gate_asset_digest",
        lambda tag, asset: _NEW_DIGEST if tag == "v0.10.3" else None,
    )

    changes, _behind = toolchains.apply_toolchains(_ws(tmp_path, "rust", "cargo"))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [("gate", "0.10.1", "0.10.3")]
    text = wf.read_text()
    assert "releases/download/v0.10.3/gate-linux-amd64" in text
    assert f'echo "{_NEW_DIGEST}  $RUNNER_TEMP/gate" | sha256sum -c -' in text


@pytest.mark.usefixtures("_gate_feeds")
def test_url_gate_pin_bump_refused_without_a_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No digest means no bump: half a pin fails the checksum, so report a failure."""
    body = _download_step()
    wf = _workflow(tmp_path, body)
    monkeypatch.setattr(toolchains, "_gate_asset_digest", lambda tag, asset: None)

    changes, behind = toolchains.apply_toolchains(_ws(tmp_path, "rust", "cargo"))

    assert changes == []
    assert behind == []
    assert wf.read_text() == body
    assert toolchains.failed_pin_count() == 1
    assert "FAIL toolchains" in capsys.readouterr().err
