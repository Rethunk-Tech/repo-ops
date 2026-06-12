"""Upgrade-local constants, regexes, and timeouts."""

from __future__ import annotations

import os
import re

from repo_ops_core.constants import SKIP_DIRS

TIMEOUT_CHECK = 600
TIMEOUT_APPLY = 600
TIMEOUT_VERIFY = 900

# ---------------------------------------------------------------------------
# Env-overridable timeout resolvers — follow the same pattern as
# repo_ops_core.discovery._resolve_find_timeout / REPO_OPS_FIND_TIMEOUT.
# ---------------------------------------------------------------------------

_TIMEOUT_CHECK_ENV = "REPO_OPS_TIMEOUT_CHECK"
_TIMEOUT_APPLY_ENV = "REPO_OPS_TIMEOUT_APPLY"
_TIMEOUT_VERIFY_ENV = "REPO_OPS_TIMEOUT_VERIFY"


def _resolve_timeout(env_var: str, default: int) -> int:
    """Return timeout from *env_var* (positive int/float accepted), or *default*."""
    raw = os.environ.get(env_var, "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return int(value)
        except ValueError:
            pass
    return default


def resolve_timeout_check() -> int:
    """Return TIMEOUT_CHECK, overridable via ``REPO_OPS_TIMEOUT_CHECK``."""
    return _resolve_timeout(_TIMEOUT_CHECK_ENV, TIMEOUT_CHECK)


def resolve_timeout_apply() -> int:
    """Return TIMEOUT_APPLY, overridable via ``REPO_OPS_TIMEOUT_APPLY``."""
    return _resolve_timeout(_TIMEOUT_APPLY_ENV, TIMEOUT_APPLY)


def resolve_timeout_verify() -> int:
    """Return TIMEOUT_VERIFY, overridable via ``REPO_OPS_TIMEOUT_VERIFY``."""
    return _resolve_timeout(_TIMEOUT_VERIFY_ENV, TIMEOUT_VERIFY)


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
