"""Tests for repo-ops doctor (toolchain/auth preflight)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from repo_ops_doctor import doctor
from repo_ops_doctor.doctor import (
    BinarySpec,
    build_doctor_parser,
    main,
    probe_all,
    probe_binary,
    probe_gh_auth,
    probe_skill_drift,
)


def _fake_which_all_present(name: str) -> str | None:
    return f"/usr/bin/{name}"


def _fake_run_version(cmd: list[str], **_kwargs: object) -> MagicMock:
    return MagicMock(returncode=0, stdout=f"{cmd[0]} version 1.2.3\n", stderr="")


# ---------------------------------------------------------------------------
# All-present happy path
# ---------------------------------------------------------------------------


def test_probe_all_present_reports_found_with_path_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)

    results = probe_all()

    assert results  # non-empty static table
    assert {"pyrefly", "hatch", "pre-commit"} <= {r.name for r in results}
    assert all(r.found for r in results)
    for r in results:
        if r.name == "gofmt":
            # gofmt has no safe version invocation (version_args=()); skipped.
            assert r.version is None
            assert r.path == "/usr/bin/gofmt"
        elif r.name == "cargo-audit":
            assert r.found is True
            assert r.version is not None
            assert r.path == "/usr/bin/cargo"
        else:
            assert r.version is not None
            assert r.path == f"/usr/bin/{r.name}"


def test_main_all_present_exits_zero_and_prints_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)

    code = main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "binaries found" in out
    assert "gh auth: authenticated" in out
    assert "Missing:" not in out


def test_main_reports_dangling_skill_symlink_as_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)
    destination = tmp_path / ".cursor" / "skills" / "repo-ops"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(tmp_path / "missing-skill")
    monkeypatch.setattr("repo_ops.install_skill.resolve_home", lambda _home: tmp_path)
    monkeypatch.setattr(
        "repo_ops.install_skill.destinations_for",
        lambda _target, _home: [("cursor", destination)],
    )

    assert not destination.exists()
    assert destination.is_symlink()
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Warning: installed skill drift detected:" in out
    assert str(destination) in out
    assert "Run: repo-ops install-skill --force" in out

    assert main(["--json", "--compact"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill_drift"] == [str(destination)]


def test_main_reports_mutated_skill_content_as_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from repo_ops.install_skill import main as install_skill_main

    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)
    destination = tmp_path / ".cursor" / "skills" / "repo-ops"
    assert install_skill_main(["--dest", str(destination)]) == 0
    monkeypatch.setattr("repo_ops.install_skill.resolve_home", lambda _home: tmp_path)
    monkeypatch.setattr(
        "repo_ops.install_skill.destinations_for",
        lambda _target, _home: [("cursor", destination)],
    )

    skill_file = destination / "SKILL.md"
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nmutated\n")

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Warning: installed skill drift detected:" in out
    assert str(destination) in out
    assert "Run: repo-ops install-skill --force" in out

    assert main(["--json", "--compact"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill_drift"] == [str(destination)]


def test_probe_skill_drift_reports_mutated_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from repo_ops.install_skill import main as install_skill_main

    destination = tmp_path / ".cursor" / "skills" / "repo-ops"
    assert install_skill_main(["--dest", str(destination)]) == 0
    monkeypatch.setattr("repo_ops.install_skill.resolve_home", lambda _home: tmp_path)
    monkeypatch.setattr(
        "repo_ops.install_skill.destinations_for",
        lambda _target, _home: [("cursor", destination)],
    )

    assert str(destination) not in probe_skill_drift()

    skill_file = destination / "SKILL.md"
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nmutated\n")

    assert str(destination) in probe_skill_drift()


# ---------------------------------------------------------------------------
# Missing tool + --strict
# ---------------------------------------------------------------------------


def _fake_which_missing_poetry(name: str) -> str | None:
    if name == "poetry":
        return None
    return f"/usr/bin/{name}"


def test_missing_binary_reported_but_default_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_missing_poetry)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)

    code = main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "Missing: poetry" in out


def test_missing_binary_with_strict_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_missing_poetry)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)

    assert main(["--strict"]) == 1


def test_no_missing_binaries_with_strict_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)

    assert main(["--strict"]) == 0


# ---------------------------------------------------------------------------
# --json shape
# ---------------------------------------------------------------------------


def test_json_output_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_missing_poetry)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)

    code = main(["--json", "--compact"])

    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["schema_version"] == 1
    assert payload["missing"] == ["poetry"]
    assert payload["total_count"] == len(payload["binaries"])
    assert payload["found_count"] == payload["total_count"] - 1
    names = {b["name"] for b in payload["binaries"]}
    assert {
        "git",
        "gh",
        "uv",
        "pyrefly",
        "hatch",
        "poetry",
        "pre-commit",
        "npm",
        "go",
        "cargo",
        "cargo-audit",
    } <= names
    poetry_entry = next(b for b in payload["binaries"] if b["name"] == "poetry")
    assert poetry_entry == {
        "name": "poetry",
        "ecosystem": "python",
        "found": False,
        "path": None,
        "version": None,
    }
    assert payload["gh_auth"]["checked"] is True
    assert payload["gh_auth"]["authenticated"] is True


def test_json_out_writes_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)
    out_path = tmp_path / "doctor.json"

    code = main(["--json", "--json-out", str(out_path)])

    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    printed = capsys.readouterr().out.strip()
    assert printed == str(out_path)


def test_json_out_includes_skill_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from repo_ops.install_skill import main as install_skill_main

    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_version)
    destination = tmp_path / ".cursor" / "skills" / "repo-ops"
    assert install_skill_main(["--dest", str(destination)]) == 0
    capsys.readouterr()
    monkeypatch.setattr("repo_ops.install_skill.resolve_home", lambda _home: tmp_path)
    monkeypatch.setattr(
        "repo_ops.install_skill.destinations_for",
        lambda _target, _home: [("cursor", destination)],
    )

    skill_file = destination / "SKILL.md"
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nmutated\n")
    out_path = tmp_path / "doctor.json"

    assert main(["--json", "--json-out", str(out_path)]) == 0

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["skill_drift"] == [str(destination)]
    printed = capsys.readouterr().out.strip()
    assert printed == str(out_path)


def test_json_out_requires_json_flag() -> None:
    with pytest.raises(SystemExit):
        main(["--json-out", "/tmp/doctor.json"])


# ---------------------------------------------------------------------------
# gh auth: unauthenticated / missing / probe failure
# ---------------------------------------------------------------------------


def test_probe_gh_auth_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        assert cmd == ["/usr/bin/gh", "auth", "status"]
        return MagicMock(
            returncode=1, stdout="", stderr="You are not logged into any hosts"
        )

    monkeypatch.setattr(doctor.subprocess, "run", _fake_run)

    status = probe_gh_auth("/usr/bin/gh")

    assert status.checked is True
    assert status.authenticated is False
    assert status.detail == "You are not logged into any hosts"


def test_probe_gh_auth_missing_gh_skips_check() -> None:
    status = probe_gh_auth(None)
    assert status.checked is False
    assert status.authenticated is None
    assert status.detail is None


def test_probe_gh_auth_timeout_reports_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_timeout(cmd: list[str], **_kwargs: object) -> MagicMock:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=15)

    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_timeout)

    status = probe_gh_auth("/usr/bin/gh")

    assert status.checked is True
    assert status.authenticated is None
    assert status.detail is not None


def test_main_reports_gh_unauthenticated_in_human_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)

    def _fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        if cmd[1:] == ["auth", "status"]:
            return MagicMock(returncode=1, stdout="", stderr="not logged in")
        return _fake_run_version(cmd)

    monkeypatch.setattr(doctor.subprocess, "run", _fake_run)

    code = main([])
    assert code == 0
    out = capsys.readouterr().out
    assert "gh auth: NOT authenticated" in out

    code = main(["--json", "--compact"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["gh_auth"] == {
        "checked": True,
        "authenticated": False,
        "detail": "not logged in",
    }


# ---------------------------------------------------------------------------
# Individual probe helpers / edge cases
# ---------------------------------------------------------------------------


def test_probe_binary_missing_returns_no_path_or_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    result = probe_binary(BinarySpec("nonexistent-tool", "test"))
    assert result.found is False
    assert result.path is None
    assert result.version is None


def test_probe_version_failure_leaves_version_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/bin/broken-tool")

    def _fake_run_error(cmd: list[str], **_kwargs: object) -> MagicMock:
        raise OSError("exec failed")

    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_error)

    result = probe_binary(BinarySpec("broken-tool", "test"))
    assert result.found is True
    assert result.path == "/usr/bin/broken-tool"
    assert result.version is None


def test_doctor_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_build_doctor_parser_defaults() -> None:
    parser = build_doctor_parser()
    args = parser.parse_args([])
    assert args.strict is False
    assert args.json_out is False
    assert args.compact is False


def test_probe_cargo_audit_missing_when_cargo_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    result = doctor.probe_binary(BinarySpec("cargo-audit", "rust", version_args=()))
    assert result.found is False
    assert result.path is None


def test_probe_cargo_audit_missing_when_subcommand_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor.shutil,
        "which",
        lambda name: "/usr/bin/cargo" if name == "cargo" else f"/usr/bin/{name}",
    )

    def _fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        if cmd[1:] == ["audit", "--version"]:
            return MagicMock(returncode=1, stdout="", stderr="no such subcommand")
        return _fake_run_version(cmd)

    monkeypatch.setattr(doctor.subprocess, "run", _fake_run)
    result = doctor.probe_binary(BinarySpec("cargo-audit", "rust", version_args=()))
    assert result.found is False
    assert result.path == "/usr/bin/cargo"


def test_missing_cargo_audit_with_strict_exits_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor.shutil, "which", _fake_which_all_present)

    def _fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        if cmd[1:] == ["audit", "--version"]:
            return MagicMock(
                returncode=1, stdout="", stderr="cargo-audit not installed"
            )
        if cmd[1:] == ["auth", "status"]:
            return MagicMock(returncode=0, stdout="ok", stderr="")
        return _fake_run_version(cmd)

    monkeypatch.setattr(doctor.subprocess, "run", _fake_run)

    assert main(["--strict"]) == 1
