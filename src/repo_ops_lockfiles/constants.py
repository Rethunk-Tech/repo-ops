"""Lockfile filenames by ecosystem."""

from __future__ import annotations

NODE_LOCKFILES = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
    "package.json",
)
PY_LOCKFILES = (
    "uv.lock",
    "requirements.txt",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
)
GO_LOCKFILES = ("go.sum", "go.mod")
RUST_LOCKFILES = ("Cargo.lock",)
DENO_LOCKFILES = ("deno.lock",)


def manifest_names_for_manager(manager: str) -> frozenset[str]:
    """Manifest basenames for a manager (the file a bump rewrites versions in).

    Used only for root workspaces (``rel == "."``), where there is no directory
    prefix to scope by, so the manifest must be matched by name to avoid a partial
    commit (lockfile staged, manifest dropped). A subdirectory workspace's prefix
    already captures its manifest.
    """
    if manager in ("yarn", "npm", "pnpm", "bun"):
        names = {"package.json"}
        if manager == "pnpm":
            names.add("pnpm-workspace.yaml")
        return frozenset(names)
    if manager in ("uv", "hatch", "poetry", "pdm"):
        return frozenset({"pyproject.toml"})
    if manager == "pip-tools":
        return frozenset({"requirements.in"})
    if manager == "pipenv":
        return frozenset({"Pipfile"})
    if manager == "go":
        return frozenset({"go.mod", "go.work"})
    if manager == "rust":
        return frozenset({"Cargo.toml"})
    if manager == "deno":
        return frozenset({"deno.json", "deno.jsonc"})
    if manager == "precommit":
        return frozenset({".pre-commit-config.yaml", ".pre-commit-config.yml"})
    return frozenset()
