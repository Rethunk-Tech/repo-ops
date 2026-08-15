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
