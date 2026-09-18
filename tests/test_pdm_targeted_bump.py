"""Targeted constraint-bump check/apply across pdm, pipenv, poetry, pip-tools.

Each manager resolves a direct dependency's requirement string, an "add"/
"install" CLI verb, and a lockfile format differently, but the apply
contract is identical: bump the manifest constraint, re-run the manager,
and restore both manifest and lockfile unless the bump both succeeds and
lands the expected version. Cases sharing that contract are parametrized
off one ``_Spec`` per manager; pipenv's venv precondition and pip-tools'
raise-on-failure/constraints-file mechanics are genuine behavioural
differences and stay as their own tests.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_models import Change
from repo_ops_py_pdm.pdm import _pdm_requirement_satisfied, apply_pdm, check_pdm
from repo_ops_py_pip_tools.pip_tools import (
    _pip_tools_requirement_satisfied,
)
from repo_ops_py_pipenv.pipenv import (
    _apply_pipenv_targeted_bumps,
    _pipenv_requirement_satisfied,
    apply_pipenv,
    check_pipenv,
)
from repo_ops_py_poetry.poetry import (
    _poetry_requirement_satisfied,
    apply_poetry,
    check_poetry,
)


def _pdm_seed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests ^2.28.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n', encoding="utf-8"
    )


def _poetry_seed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[tool.poetry.dependencies]\nrequests = "^2.28.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n', encoding="utf-8"
    )


def _pipenv_seed(tmp_path: Path) -> None:
    (tmp_path / "Pipfile").write_text('[packages]\nrequests = "^2.28.0"\n', encoding="utf-8")
    (tmp_path / "Pipfile.lock").write_text(
        '{"default": {"requests": {"version": "==2.31.0"}}, "develop": {}}',
        encoding="utf-8",
    )


def _pip_tools_seed(tmp_path: Path) -> None:
    (tmp_path / "requirements.in").write_text("requests==2.28.0\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")


def _write_locked_version(path: Path, version: str) -> None:
    if path.name == "Pipfile.lock":
        path.write_text(
            json.dumps({"default": {"requests": {"version": f"=={version}"}}, "develop": {}}),
            encoding="utf-8",
        )
    else:
        path.write_text(
            f'[[package]]\nname = "requests"\nversion = "{version}"\n',
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Shared shape: pdm / poetry / pipenv — TOML-or-JSON lock, "<mgr> <verb>"
# targeted install, restore-or-keep manifest+lockfile.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spec:
    manager: str
    module_path: str
    seed: Callable[[Path], None]
    manifest_name: str
    lock_name: str
    add_verb: str
    which_binary: str
    check_fn: Callable[..., object]
    apply_fn: Callable[..., list[Change] | None]
    precommands: tuple[list[str], ...] = field(default_factory=tuple)

    @property
    def run_target(self) -> str:
        return f"{self.module_path}.run"

    @property
    def which_target(self) -> str:
        return f"{self.module_path}.shutil.which"

    @property
    def pypi_target(self) -> str:
        return f"{self.module_path}._pypi_latest"

    @property
    def add_cmd_prefix(self) -> list[str]:
        return [self.manager, self.add_verb]

    @property
    def expected_calls(self) -> list[list[str]]:
        return [
            [self.manager, "update"],
            *self.precommands,
            [*self.add_cmd_prefix, "requests@^3.0.0"],
        ]


PDM = _Spec(
    "pdm",
    "repo_ops_py_pdm.pdm",
    _pdm_seed,
    "pyproject.toml",
    "pdm.lock",
    "add",
    "/usr/bin/pdm",
    check_pdm,
    apply_pdm,
)
POETRY = _Spec(
    "poetry",
    "repo_ops_py_poetry.poetry",
    _poetry_seed,
    "pyproject.toml",
    "poetry.lock",
    "add",
    "/usr/bin/poetry",
    check_poetry,
    apply_poetry,
)
PIPENV = _Spec(
    "pipenv",
    "repo_ops_py_pipenv.pipenv",
    _pipenv_seed,
    "Pipfile",
    "Pipfile.lock",
    "install",
    "/usr/bin/pipenv",
    check_pipenv,
    apply_pipenv,
    precommands=(["pipenv", "--venv"],),
)

_ALL_SPECS = [
    pytest.param(PDM, id="pdm"),
    pytest.param(POETRY, id="poetry"),
    pytest.param(PIPENV, id="pipenv"),
]
_TOML_SPECS = [pytest.param(PDM, id="pdm"), pytest.param(POETRY, id="poetry")]


@contextmanager
def _spec_patched(
    spec: _Spec, fake_run: Callable[..., subprocess.CompletedProcess[str]]
) -> Iterator[None]:
    with (
        patch(spec.which_target, return_value=spec.which_binary),
        patch(spec.run_target, side_effect=fake_run),
        patch(spec.pypi_target, return_value="3.0.0"),
    ):
        yield


# ---------------------------------------------------------------------------
# requirement_satisfied wiring — each manager's default operator differs
# ---------------------------------------------------------------------------

_REQUIREMENT_CASES = [
    pytest.param(
        _pdm_requirement_satisfied,
        [
            ("2.31.0", "2.28.0", True),
            ("3.0.0", "2.28.0", False),
            ("2.31.0", "==2.28.0", False),
        ],
        id="pdm",
    ),
    pytest.param(
        _poetry_requirement_satisfied,
        [
            ("2.31.0", "2.28.0", True),
            ("3.0.0", "2.28.0", False),
            ("2.31.0", "==2.28.0", False),
        ],
        id="poetry",
    ),
    pytest.param(
        _pipenv_requirement_satisfied,
        [
            ("2.31.0", "^2.28.0", True),
            ("3.0.0", "^2.28.0", False),
            ("2.31.0", "==2.28.0", False),
        ],
        id="pipenv",
    ),
    pytest.param(
        _pip_tools_requirement_satisfied,
        [
            ("2.31.0", "==2.31.0", True),
            ("3.0.0", "==2.31.0", False),
            ("3.0.0", ">=2.31.0", True),
        ],
        id="pip-tools",
    ),
]


@pytest.mark.parametrize(("fn", "cases"), _REQUIREMENT_CASES)
def test_requirement_satisfied(fn: Callable[[str, str], bool], cases) -> None:
    for version, requirement, expected in cases:
        assert fn(version, requirement) is expected


# ---------------------------------------------------------------------------
# check_* — reports the direct-dependency latest-version bump candidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", _TOML_SPECS)
def test_check_reports_latest_direct_dependency(
    tmp_path: Path, make_workspace, spec: _Spec
) -> None:
    spec.seed(tmp_path)

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with _spec_patched(spec, fake_run):
        result = spec.check_fn(
            make_workspace(tmp_path, ecosystem="py", manager=spec.manager, label="demo")
        )

    assert result == ([], [Change("requests", "2.31.0", "3.0.0")])


# ---------------------------------------------------------------------------
# apply_* — targeted bump: restore-on-failure / update-on-success
# ---------------------------------------------------------------------------


# A targeted bump command can misbehave two ways: the command itself fails
# (returncode != 0), or it exits clean but the lockfile lands a version other
# than the one requested (e.g. a concurrent resolver constraint). Both must
# restore the manifest and lockfile untouched. pipenv's own venv-precondition
# test already exercises its failure path, so only pdm/poetry get the second.
_FAILURE_CASES = [
    pytest.param(PDM, 1, None, id="pdm-add-fails"),
    pytest.param(POETRY, 1, None, id="poetry-add-fails"),
    pytest.param(PIPENV, 1, None, id="pipenv-install-fails"),
    pytest.param(PDM, 0, "2.32.0", id="pdm-wrong-version-lands"),
    pytest.param(POETRY, 0, "2.32.0", id="poetry-wrong-version-lands"),
]


@pytest.mark.parametrize(("spec", "returncode", "landed_version"), _FAILURE_CASES)
def test_apply_restores_files_on_targeted_bump_failure(
    tmp_path: Path,
    make_workspace,
    spec: _Spec,
    returncode: int,
    landed_version: str | None,
) -> None:
    spec.seed(tmp_path)
    manifest = tmp_path / spec.manifest_name
    lockfile = tmp_path / spec.lock_name
    manifest_before = manifest.read_bytes()
    lock_before = lockfile.read_bytes()

    def fake_run(cmd, cwd, timeout=None):
        if cmd[: len(spec.add_cmd_prefix)] == spec.add_cmd_prefix:
            manifest.write_text("changed\n", encoding="utf-8")
            if landed_version:
                lockfile.write_text(
                    f'[[package]]\nname = "requests"\nversion = "{landed_version}"\n',
                    encoding="utf-8",
                )
            else:
                lockfile.write_text("changed\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="failed")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with _spec_patched(spec, fake_run):
        remaining = spec.apply_fn(
            make_workspace(tmp_path, ecosystem="py", manager=spec.manager, label="demo")
        )

    assert remaining == [Change("requests", "2.31.0", "3.0.0")]
    assert manifest.read_bytes() == manifest_before
    assert lockfile.read_bytes() == lock_before


@pytest.mark.parametrize("spec", _ALL_SPECS)
def test_apply_keeps_successful_targeted_bump(tmp_path: Path, make_workspace, spec: _Spec) -> None:
    spec.seed(tmp_path)
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))
        if cmd[: len(spec.add_cmd_prefix)] == spec.add_cmd_prefix:
            _write_locked_version(tmp_path / spec.lock_name, "3.0.0")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with _spec_patched(spec, fake_run):
        remaining = spec.apply_fn(
            make_workspace(tmp_path, ecosystem="py", manager=spec.manager, label="demo")
        )

    assert remaining == []
    assert calls == spec.expected_calls


# ---------------------------------------------------------------------------
# pipenv — venv precondition has no pdm/poetry equivalent
# ---------------------------------------------------------------------------


def test_targeted_pipenv_bump_skips_missing_pipfile_constraint(
    tmp_path: Path, make_workspace
) -> None:
    _pipenv_seed(tmp_path)
    candidate = Change("flask", "2.3.0", "3.0.0")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")

    with patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run):
        remaining = _apply_pipenv_targeted_bumps(
            make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"),
            [candidate],
        )

    assert remaining == [candidate]
    assert calls == [["pipenv", "--venv"]]


# ---------------------------------------------------------------------------
# pip-tools — constraints-file prevention, not a post-hoc restore; apply
# raises on a failed targeted compile rather than returning it as remaining
# ---------------------------------------------------------------------------
