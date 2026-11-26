"""repo-ops re-runs its sweeps in their own memory-capped scope."""

from __future__ import annotations

from unittest.mock import patch

from repo_ops import cli


def _argv(
    args: list[str], *, cgroup: str = "0::/user.slice/app.slice/session.scope\n"
) -> list[str] | None:
    with (
        patch.object(cli.Path, "read_text", return_value=cgroup),
        patch.object(cli.shutil, "which", return_value="/usr/bin/systemd-run"),
        patch.dict(cli.os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}),
    ):
        return cli._scope_argv(args)


def test_apply_reruns_in_a_capped_scope() -> None:
    argv = _argv(["apply", "/some/repo"])
    assert argv is not None
    assert argv[:4] == ["systemd-run", "--user", "--scope", "-q"]
    assert "MemoryMax=24G" in argv
    assert argv[-4:] == ["-m", "repo_ops.cli", "apply", "/some/repo"]


def test_already_scoped_runs_in_place() -> None:
    assert _argv(["apply"], cgroup="0::/user.slice/app.slice/repo-ops-42.scope\n") is None


def test_light_commands_run_in_place() -> None:
    assert _argv(["report"]) is None
    assert _argv(["--version"]) is None
    assert _argv([]) is None


def test_gates_reruns_in_a_capped_scope() -> None:
    argv = _argv(["gates"])
    assert argv is not None
    assert argv[-3:] == ["-m", "repo_ops.cli", "gates"]
