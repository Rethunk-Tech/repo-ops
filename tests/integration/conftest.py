"""Shared fixtures for integration tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from repo_ops_models import Change, Workspace

IS_ODD_PIN = "0.1.2"
IS_ODD_RANGE = ">=0.1.0 <2.0.0"
IS_ODD_TARGET = "1.0.0"
NPM_REGISTRY = "https://registry.npmjs.org"

_COREPACK = shutil.which("corepack")
_COREPACK_OK = False
if _COREPACK is not None:
    try:
        _probe = subprocess.run(
            [_COREPACK, "yarn", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        _COREPACK_OK = _probe.returncode == 0
    except Exception:
        _COREPACK_OK = False

COREPACK_SKIP_REASON = (
    "corepack not on PATH"
    if _COREPACK is None
    else "corepack yarn --version probe failed"
)


def skip_on_install_failure(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != 0:
        pytest.skip(f"{label} failed (network?): {proc.stderr.strip()[:200]}")


def read_is_odd_version(root: Path) -> str:
    pkg_json = root / "node_modules" / "is-odd" / "package.json"
    if not pkg_json.exists():
        pytest.skip("node_modules/is-odd not created")
    return json.loads(pkg_json.read_text(encoding="utf-8"))["version"]


def ghost_workspace(root: Path, *, manager: str, label: str) -> Workspace:
    return Workspace(
        path=root,
        ecosystem="node",
        manager=manager,
        git_repo=root,
        label=label,
    )


def write_npm_ghost_package(root: Path, *, name: str = "npm-ghost-test") -> None:
    pkg: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "dependencies": {"is-odd": IS_ODD_PIN},
    }
    (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


def write_bun_ghost_package(root: Path, *, name: str = "ghost-test") -> None:
    pkg: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "dependencies": {"is-odd": IS_ODD_RANGE},
        "overrides": {"is-odd": IS_ODD_PIN},
    }
    (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


def write_yarn_ghost_package(root: Path, *, name: str) -> None:
    (root / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")
    pkg: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "packageManager": "yarn@4.15.0",
        "dependencies": {"is-odd": IS_ODD_RANGE},
        "resolutions": {"is-odd": IS_ODD_PIN},
    }
    (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


def write_pnpm_ghost_package(root: Path, *, name: str = "pnpm-ghost-test") -> None:
    """Write a pnpm workspace pinned to is-odd@0.1.2 for ghost-bump tests."""
    pkg: dict[str, Any] = {
        "name": name,
        "version": "1.0.0",
        "dependencies": {"is-odd": IS_ODD_PIN},
        "pnpm": {"overrides": {"is-odd": IS_ODD_PIN}},
    }
    (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    (root / "pnpm-workspace.yaml").write_text(
        "packages:\n  - '.'\noverrides:\n  is-odd: 0.1.2\n",
        encoding="utf-8",
    )


def run_npm_install(root: Path, *, timeout: int = 120) -> None:
    npm = shutil.which("npm")
    assert npm is not None
    proc = subprocess.run(
        [npm, "install", "--registry", NPM_REGISTRY],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    skip_on_install_failure(proc, "npm install")


def run_bun_install(root: Path, *, timeout: int = 60) -> None:
    bun = shutil.which("bun")
    assert bun is not None
    proc = subprocess.run(
        [bun, "install", "--registry", NPM_REGISTRY],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    skip_on_install_failure(proc, "bun install")


def run_yarn_install(root: Path, *, timeout: int = 90) -> None:
    assert _COREPACK is not None
    proc = subprocess.run(
        [_COREPACK, "yarn", "install"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    skip_on_install_failure(proc, "yarn install")


def run_pnpm_install(root: Path, *, timeout: int = 120) -> None:
    pnpm = shutil.which("pnpm")
    assert pnpm is not None
    proc = subprocess.run(
        [pnpm, "install", "--registry", NPM_REGISTRY],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    skip_on_install_failure(proc, "pnpm install")


@pytest.fixture
def npm_ghost_root() -> Iterator[Path]:
    """Temp dir with npm-installed is-odd@0.1.2."""
    npm = shutil.which("npm")
    if npm is None:
        pytest.skip("npm not on PATH")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        write_npm_ghost_package(root)
        run_npm_install(root)
        yield root


@pytest.fixture
def bun_ghost_root() -> Iterator[Path]:
    """Temp dir with bun-installed is-odd@0.1.2 held by overrides."""
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("bun not on PATH")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        write_bun_ghost_package(root)
        run_bun_install(root)
        yield root


@pytest.fixture
def pnpm_ghost_root() -> Iterator[Path]:
    """Temp dir with pnpm-installed is-odd@0.1.2."""
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        pytest.skip("pnpm not on PATH")
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        write_pnpm_ghost_package(root)
        run_pnpm_install(root)
        yield root


@pytest.fixture
def yarn_ghost_root() -> Iterator[Path]:
    """Temp dir with yarn4 node-modules install of is-odd@0.1.2 (resolutions pin)."""
    if not _COREPACK_OK:
        pytest.skip(COREPACK_SKIP_REASON)
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        write_yarn_ghost_package(root, name="yarn-ghost-test")
        run_yarn_install(root)
        yield root


@pytest.fixture
def yarn_ghost_apply_root() -> Iterator[Path]:
    """Yarn ghost workspace for apply_yarn integration."""
    if not _COREPACK_OK:
        pytest.skip(COREPACK_SKIP_REASON)
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        write_yarn_ghost_package(root, name="yarn-ghost-apply-test")
        run_yarn_install(root)
        yield root


@pytest.fixture
def is_odd_ghost_change() -> list[Change]:
    return [Change("is-odd", IS_ODD_PIN, IS_ODD_TARGET)]
