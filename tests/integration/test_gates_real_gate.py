"""``repo-ops gates`` against the real ``gate`` binary: a failing repo is named."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repo_ops_gates.gates import main_gates

pytestmark = pytest.mark.integration

_GATE_TOML = """\
[gates.lint]
run = "sh -c 'echo broken >&2; exit 1'"

[gates.build]
run = "sh -c 'exit 0'"
"""


@pytest.mark.skipif(shutil.which("gate") is None, reason="gate is not on PATH")
def test_a_repo_whose_gate_fails_exits_non_zero_and_names_it(
    tmp_path: Path, capsys, init_git_repo
) -> None:
    init_git_repo(tmp_path, commit=True, filename=".gate.toml", content=_GATE_TOML)

    code = main_gates([str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "failing           1" in out
    assert "FAIL" in out
    # The failing gate's command is named, the passing one is not.
    assert "exit 1" in out or "lint" in out


@pytest.mark.skipif(shutil.which("gate") is None, reason="gate is not on PATH")
def test_a_repo_with_no_gate_declared_is_not_a_failure(
    tmp_path: Path, capsys, init_git_repo
) -> None:
    init_git_repo(tmp_path, commit=True)

    code = main_gates([str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "no gate declared  1" in out
