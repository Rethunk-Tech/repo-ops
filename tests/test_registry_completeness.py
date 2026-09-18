"""Registry completeness harness — would have caught the pdm/pipenv/deno dead-end bug.

Asserts that every manager/ecosystem that discovery can emit is:
  1. Registered in repo_ops_upgrade.registry.CHECKERS and APPLIERS after register_all()
  2. Present in MANAGER_FILTER_CHOICES (CLI filter parity)
  3. Present in ECO_FILTER_CHOICES when the manager maps to a new ecosystem

Also adds one behavioral end-to-end test per new ecosystem (pdm/pipenv/deno) that
drives check_workspace through register_all() with a mocked subprocess, proving
the manager is reachable via the registry (not just by calling check_* directly).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops._register import register_all
from repo_ops_core.cli import ECO_FILTER_CHOICES, MANAGER_FILTER_CHOICES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _registered_managers() -> tuple[set[str], set[str]]:
    """Return (CHECKERS keys, APPLIERS keys) after a fresh register_all()."""
    # Re-import to get a fresh view after registration.
    from repo_ops_upgrade import registry

    register_all()
    return set(registry.CHECKERS), set(registry.APPLIERS)


# ---------------------------------------------------------------------------
# Core registry completeness
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    """All managers that discovery can emit must be registered."""

    def setup_method(self) -> None:
        register_all()
        from repo_ops_upgrade import registry

        self._checkers: set[str] = set(registry.CHECKERS)
        self._appliers: set[str] = set(registry.APPLIERS)

    # Managers added in this fix
    @pytest.mark.parametrize("manager", ["pdm", "pipenv", "deno"])
    def test_new_manager_has_checker(self, manager: str) -> None:
        """After register_all(), pdm/pipenv/deno must have a checker."""
        assert manager in self._checkers, (
            f"register_all() did not register checker for {manager!r}; "
            f"CHECKERS={sorted(self._checkers)}"
        )

    @pytest.mark.parametrize("manager", ["pdm", "pipenv", "deno"])
    def test_new_manager_has_applier(self, manager: str) -> None:
        """After register_all(), pdm/pipenv/deno must have an applier."""
        assert manager in self._appliers, (
            f"register_all() did not register applier for {manager!r}; "
            f"APPLIERS={sorted(self._appliers)}"
        )

    def test_manager_filter_choices_covers_all_checkers(self) -> None:
        """MANAGER_FILTER_CHOICES must equal CHECKERS keys (CLI parity)."""
        assert set(MANAGER_FILTER_CHOICES) == self._checkers, (
            f"MANAGER_FILTER_CHOICES out of sync with CHECKERS.\n"
            f"  Missing from choices: {self._checkers - set(MANAGER_FILTER_CHOICES)}\n"
            f"  Extra in choices:     {set(MANAGER_FILTER_CHOICES) - self._checkers}"
        )

    def test_deno_in_eco_filter_choices(self) -> None:
        """deno ecosystem must appear in ECO_FILTER_CHOICES."""
        assert "deno" in ECO_FILTER_CHOICES, (
            f"'deno' missing from ECO_FILTER_CHOICES: {ECO_FILTER_CHOICES}"
        )


# ---------------------------------------------------------------------------
# Behavioral end-to-end tests via check_workspace
# ---------------------------------------------------------------------------


class TestCheckWorkspaceReachability:
    """Each new manager must be reachable through check_workspace after register_all()."""

    def setup_method(self) -> None:
        register_all()

    def test_check_workspace_pdm_reachable(self, tmp_path: Path, make_workspace) -> None:
        """check_workspace routes correctly to check_pdm after register_all()."""
        from repo_ops_upgrade.registry import check_workspace

        def fake_run(cmd, cwd, timeout=None):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
            patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
        ):
            result = check_workspace(
                make_workspace(tmp_path, manager="pdm", ecosystem="py", label="test-pdm")
            )

        assert result.status != "skip", (
            f"pdm workspace was skipped — manager not registered: {result.error}"
        )
        assert result.error is None or "unsupported" not in result.error, (
            f"pdm checker not found in registry: {result.error}"
        )

    def test_check_workspace_pipenv_reachable(self, tmp_path: Path, make_workspace) -> None:
        """check_workspace routes correctly to check_pipenv after register_all()."""
        from repo_ops_upgrade.registry import check_workspace

        call_n = 0

        def fake_run(cmd, cwd, timeout=None):
            nonlocal call_n
            call_n += 1
            if call_n == 1:
                # venv probe succeeds
                return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
            # pip list returns empty JSON array
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

        with (
            patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
            patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
        ):
            result = check_workspace(
                make_workspace(tmp_path, manager="pipenv", ecosystem="py", label="test-pipenv")
            )

        assert result.status != "skip", (
            f"pipenv workspace was skipped — manager not registered: {result.error}"
        )
        assert result.error is None or "unsupported" not in result.error, (
            f"pipenv checker not found in registry: {result.error}"
        )

    def test_check_workspace_deno_reachable(self, tmp_path: Path, make_workspace) -> None:
        """check_workspace routes correctly to check_deno after register_all()."""
        from repo_ops_upgrade.registry import check_workspace

        def fake_run(cmd, cwd, timeout=None):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
            patch("repo_ops_js.deno.run", side_effect=fake_run),
        ):
            result = check_workspace(
                make_workspace(tmp_path, manager="deno", ecosystem="deno", label="test-deno")
            )

        assert result.status != "skip", (
            f"deno workspace was skipped — manager not registered: {result.error}"
        )
        assert result.error is None or "unsupported" not in result.error, (
            f"deno checker not found in registry: {result.error}"
        )
