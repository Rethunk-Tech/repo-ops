"""Upgrade-local constants and regexes.

Timeout resolvers (resolve_timeout_check / resolve_timeout_apply /
resolve_timeout_verify) and the raw TIMEOUT_* defaults now live in
repo_ops_core.constants, which is a legitimate downward dependency for all
packages.  The private env-var name strings (_TIMEOUT_*_ENV) are also there
so that verify.py's per-repo config layer can read them without reaching up.
"""

from __future__ import annotations

import re

from repo_ops_core.constants import (
    _TIMEOUT_APPLY_ENV,
    _TIMEOUT_CHECK_ENV,
    _TIMEOUT_VERIFY_ENV,
    SKIP_DIRS,
    TIMEOUT_APPLY,
    TIMEOUT_CHECK,
    TIMEOUT_VERIFY,
    resolve_timeout_apply,
    resolve_timeout_check,
    resolve_timeout_verify,
)

__all__ = [
    "BIOME_PKG",
    "CODEGEN_TRIGGERS",
    "GO_UPGRADE_RE",
    "PLAYWRIGHT_PKG",
    "TIMEOUT_APPLY",
    "TIMEOUT_CHECK",
    "TIMEOUT_VERIFY",
    "YARN_ISOLATED_COPY_SKIP",
    "YARN_ISOLATED_YARN_SKIP",
    "YARN_PEER_WARN",
    "_TIMEOUT_APPLY_ENV",
    "_TIMEOUT_CHECK_ENV",
    "_TIMEOUT_VERIFY_ENV",
    "resolve_timeout_apply",
    "resolve_timeout_check",
    "resolve_timeout_verify",
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
