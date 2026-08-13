"""Parser unit tests for PDM, pipenv, uv, and Deno lockfiles."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from repo_ops_lockfiles.parsers import (
    versions_deno_lock,
    versions_pdm_lock,
    versions_pipenv_lock,
    versions_uv_lock,
)

# ---------------------------------------------------------------------------
# PDM
# ---------------------------------------------------------------------------

_PDM_LOCK_TOML = """\
[metadata]
groups = ["default", "dev"]
strategy = ["cross_platform"]
lock_version = "4.4.1"
requires_python = ">=3.10"
content_hash = "sha256:abc"

[[package]]
name = "requests"
version = "2.31.0"
requires_python = ">=3.7"

[[package]]
name = "certifi"
version = "2023.7.22"
"""


def test_versions_pdm_lock_basic():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pdm.lock"
        p.write_text(_PDM_LOCK_TOML)
        result = versions_pdm_lock(p)
    assert "requests@2.31.0" in result
    assert "certifi@2023.7.22" in result


def test_versions_pdm_lock_empty_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pdm.lock"
        p.write_text("")
        result = versions_pdm_lock(p)
    assert result == set()


def test_versions_pdm_lock_invalid_toml():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pdm.lock"
        p.write_text("not toml {{{{")
        result = versions_pdm_lock(p)
    assert result == set()


# ---------------------------------------------------------------------------
# uv
# ---------------------------------------------------------------------------

_UV_LOCK_TOML = """\
version = 1
revision = 3
requires-python = ">=3.12"

[[package]]
name = "fastapi"
version = "0.139.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "ruff"
version = "0.15.22"
source = { registry = "https://pypi.org/simple" }
"""


def test_versions_uv_lock_basic():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "uv.lock"
        p.write_text(_UV_LOCK_TOML)
        result = versions_uv_lock(p)
    assert "fastapi@0.139.2" in result
    assert "ruff@0.15.22" in result


def test_versions_uv_lock_nested_dependency_name_does_not_override_package():
    lock = """\
version = 1

[[package]]
name = "requests"
version = "2.31.0"

[[package.dependencies]]
name = "idna"

[[package]]
name = "idna"
version = "3.6"
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "uv.lock"
        p.write_text(lock)
        result = versions_uv_lock(p)
    assert result == {"requests@2.31.0", "idna@3.6"}


def test_versions_uv_lock_empty_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "uv.lock"
        p.write_text("")
        result = versions_uv_lock(p)
    assert result == set()


def test_versions_uv_lock_invalid_toml():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "uv.lock"
        p.write_text("not toml {{{{")
        result = versions_uv_lock(p)
    assert result == set()


# ---------------------------------------------------------------------------
# pipenv
# ---------------------------------------------------------------------------

_PIPFILE_LOCK = {
    "_meta": {
        "hash": {"sha256": "abc"},
        "pipfile-spec": 6,
        "requires": {"python_version": "3.10"},
    },
    "default": {
        "requests": {"version": "==2.31.0", "hashes": []},
        "certifi": {"version": "==2023.7.22", "hashes": []},
    },
    "develop": {
        "pytest": {"version": "==7.4.0", "hashes": []},
    },
}


def test_versions_pipenv_lock_both_sections():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "Pipfile.lock"
        p.write_text(json.dumps(_PIPFILE_LOCK))
        result = versions_pipenv_lock(p)
    assert "requests@2.31.0" in result
    assert "certifi@2023.7.22" in result
    assert "pytest@7.4.0" in result


def test_versions_pipenv_lock_strips_eq_prefix():
    """Double-equals (==) version prefix must be stripped from the token."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "Pipfile.lock"
        p.write_text(
            json.dumps({"default": {"foo": {"version": "==1.2.3"}}, "develop": {}})
        )
        result = versions_pipenv_lock(p)
    assert "foo@1.2.3" in result
    assert "foo@==1.2.3" not in result


def test_versions_pipenv_lock_invalid_json():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "Pipfile.lock"
        p.write_text("not json {{{")
        result = versions_pipenv_lock(p)
    assert result == set()


# ---------------------------------------------------------------------------
# Deno
# ---------------------------------------------------------------------------

_DENO_LOCK_V3 = {
    "version": "3",
    "packages": {
        "specifiers": {
            "jsr:@std/assert@^0.221.0": "jsr:@std/assert@0.221.0",
            "npm:chalk@5": "npm:chalk@5.3.0",
        },
        "jsr": {
            "@std/assert@0.221.0": {
                "integrity": "sha256:abc",
            }
        },
        "npm": {
            "chalk@5.3.0": {
                "integrity": "sha512-xyz",
            }
        },
    },
}


def test_versions_deno_lock_v3_specifiers():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "deno.lock"
        p.write_text(json.dumps(_DENO_LOCK_V3))
        result = versions_deno_lock(p)
    # Resolved specifiers must appear in the output
    assert "jsr:@std/assert@0.221.0" in result
    assert "npm:chalk@5.3.0" in result


def test_versions_deno_lock_v3_npm_section():
    """npm sub-dict keys are also emitted (with npm: prefix)."""
    data = {
        "version": "3",
        "packages": {},
        "npm": {"lodash@4.17.21": {"integrity": "x"}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "deno.lock"
        p.write_text(json.dumps(data))
        result = versions_deno_lock(p)
    assert "npm:lodash@4.17.21" in result


def test_versions_deno_lock_empty():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "deno.lock"
        p.write_text("{}")
        result = versions_deno_lock(p)
    assert result == set()


def test_versions_deno_lock_invalid_json():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "deno.lock"
        p.write_text("not json")
        result = versions_deno_lock(p)
    assert result == set()
