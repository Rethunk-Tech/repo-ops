"""Direct-dependency requirement helpers shared by the pdm, uv and hatch managers."""

from __future__ import annotations

import re

from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.holds import HoldRule
from repo_ops_core.requirement import requirement_satisfied as _requirement_satisfied
from repo_ops_core.version_utils import hold_ceiling_spec
from repo_ops_models import Workspace
from repo_ops_py.pypi import pypi_latest as _pypi_latest


def direct_dependencies(ws: Workspace) -> dict[str, str]:
    document = read_toml_file(ws.path / "pyproject.toml") or {}
    dependencies: dict[str, str] = {}
    project = document.get("project")
    if isinstance(project, dict):
        raw_dependencies = project.get("dependencies")
        if isinstance(raw_dependencies, list):
            for raw_requirement in raw_dependencies:
                if not isinstance(raw_requirement, str):
                    continue
                match = re.match(
                    r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(.*)$",
                    raw_requirement,
                )
                if match:
                    dependencies[match.group(1)] = match.group(2).strip()
    tool = document.get("tool")
    pdm = tool.get("pdm") if isinstance(tool, dict) else None
    pdm_dependencies = pdm.get("dependencies") if isinstance(pdm, dict) else None
    if isinstance(pdm_dependencies, dict):
        for name, requirement in pdm_dependencies.items():
            if isinstance(name, str) and isinstance(requirement, str):
                dependencies[name] = requirement.strip()
    return dependencies


def latest_version(package: str) -> str:
    latest = _pypi_latest(package)
    if latest is None:
        raise RuntimeError(f"registry lookup failed for {package}")
    return latest


def requirement_satisfied(version: str, requirement: str) -> bool:
    return _requirement_satisfied(version, requirement, default_operator="^")


def bumped_requirement(requirement: str, latest: str, hold: HoldRule | None = None) -> str:
    """The requirement that admits *latest*; under a hold it also keeps the hold's ceiling."""
    stripped = requirement.strip()
    if hold is not None:
        return f">={latest},{hold_ceiling_spec(hold.max_version)}"
    if stripped.startswith("^"):
        return f"^{latest}"
    # PEP 440 has no ``~X``; a compatible-release pin keeps its operator.
    if stripped.startswith("~="):
        return f"~={latest}"
    if stripped.startswith("~"):
        return f"~{latest}"
    if stripped.startswith("=="):
        return f"=={latest}"
    # A range that excluded latest keeps room for patch releases instead of freezing at one.
    return f">={latest}"
