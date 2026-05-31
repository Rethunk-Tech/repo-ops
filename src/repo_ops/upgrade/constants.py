"""Upgrade-local constants, regexes, and timeouts."""

from __future__ import annotations

import re

TIMEOUT_CHECK = 600
TIMEOUT_APPLY = 600
TIMEOUT_VERIFY = 900

YARN_ISOLATED_COPY_SKIP = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    "out",
    ".turbo",
    ".cache",
    "coverage",
    ".nuxt",
    ".svelte-kit",
    ".dep_refresh_venv",
    "venv",
    ".venv",
}
YARN_ISOLATED_YARN_SKIP = {
    "unplugged",
    "install-state.gz",
    "build-state.yml",
}

GO_UPGRADE_RE = re.compile(r"\[([^\]]+)\]")

USES_RE = re.compile(
    r"^\s*uses:\s+([^\s@]+)@(v[\d.]+|[0-9a-f]{7,40})\s*$",
    re.MULTILINE,
)

ACTION_ALLOWLIST: dict[str, str] = {
    "actions/checkout": "v4",
    "actions/setup-node": "v4",
    "actions/setup-go": "v5",
    "actions/upload-artifact": "v4",
    "actions/download-artifact": "v4",
    "actions/cache": "v4",
    "oven-sh/setup-bun": "v2",
}

CODEGEN_TRIGGERS = (
    "@grpc/",
    "grpc-tools",
    "@accility/protoc",
    "protobufjs",
    "swagger-",
    "openapi-typescript",
    "@openapitools/",
)

BIOME_PKG = "@biomejs/biome"
PLAYWRIGHT_PKG = "@playwright/test"

YARN_PEER_WARN = re.compile(r"YN00(60|02|86)")

NODE_LOCKFILES = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
    "package.json",
)
PY_LOCKFILES = ("uv.lock", "requirements.txt", "poetry.lock")
GO_LOCKFILES = ("go.sum", "go.mod")
RUST_LOCKFILES = ("Cargo.lock",)
