"""Upgrade-local constants and regexes.

Timeout resolvers (resolve_timeout_check / resolve_timeout_apply /
resolve_timeout_verify) and the raw TIMEOUT_* defaults live in
repo_ops_core.constants; import them from there directly.
"""

from __future__ import annotations

import re

from repo_ops_core.constants import SKIP_DIRS

__all__ = [
    "BIOME_PKG",
    "CODEGEN_TRIGGERS",
    "GO_UPGRADE_RE",
    "PLAYWRIGHT_PKG",
    "YARN_ISOLATED_COPY_SKIP",
    "YARN_ISOLATED_YARN_SKIP",
    "YARN_PEER_WARN",
]

# Yarn isolated snapshots: walk exclusions plus upgrade temp venv (keep `.yarn`).
YARN_ISOLATED_COPY_SKIP = (SKIP_DIRS - {".yarn"}) | {".dep_refresh_venv"}
YARN_ISOLATED_YARN_SKIP = {
    "cache",
    "unplugged",
    "install-state.gz",
    "build-state.yml",
}

GO_UPGRADE_RE = re.compile(r"\[([^\]]+)\]")

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
