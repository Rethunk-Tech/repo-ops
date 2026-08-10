"""Tests for the Go/Rust/Deno license extractors and the unknown-exclusion gate.

Uses fixture caches (a fake GOMODCACHE, a fake DENO_DIR) and monkeypatched
``shutil.which``/``subprocess.run`` for the Rust path — no real network, no
dependency on the host having cargo/go/deno installed. No subprocess calls
escape the test process.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from repo_ops_lockfiles.license_enrich import (
    _cache_path,
    classify_license_text,
    enrich_license,
    normalize_spdx,
)
from repo_ops_lockfiles.license_policy import (
    UNKNOWN_LICENSE,
    PackageLicense,
    _classify_license_text,
    _fail_gate_unknown,
    _go_escape,
    resolve_deno_npm_license,
    resolve_go_license,
    scan_deno_licenses,
    scan_go_licenses,
    scan_rust_licenses,
)

# ---------------------------------------------------------------------------
# Go: _go_escape / _classify_license_text / resolve_go_license / scan_go_licenses
# ---------------------------------------------------------------------------


def test_go_escape_uppercase_and_bang() -> None:
    assert _go_escape("github.com/BurntSushi/toml") == "github.com/!burnt!sushi/toml"
    assert _go_escape("plain/lowercase") == "plain/lowercase"
    assert _go_escape("a!b") == "a!!b"


def test_classify_license_text_mit_and_apache() -> None:
    mit_text = (
        "MIT License\n\nPermission is hereby granted, free of charge, to any "
        "person obtaining a copy of this software..."
    )
    assert _classify_license_text(mit_text) == "MIT"

    apache_text = (
        "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/"
    )
    assert _classify_license_text(apache_text) == "Apache-2.0"

    assert _classify_license_text("some unrecognisable text") == UNKNOWN_LICENSE


def test_classify_license_text_distinguishes_bsd2_and_bsd3() -> None:
    bsd2_text = (
        "Redistribution and use in source and binary forms, with or without "
        "modification, are permitted provided that the following conditions "
        "are met.\n\n"
        "1. Redistributions of source code must retain the above copyright "
        "notice, this list of conditions and the following disclaimer.\n\n"
        "2. Redistributions in binary form must reproduce the above copyright "
        "notice, this list of conditions and the following disclaimer in the "
        "documentation and/or other materials provided with the distribution.\n\n"
        "THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "
        '"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT '
        "LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS "
        "FOR A PARTICULAR PURPOSE ARE DISCLAIMED."
    )
    assert classify_license_text(bsd2_text) == "BSD-2-Clause"
    assert _classify_license_text(bsd2_text) == "BSD-2-Clause"

    bsd3_text = (
        "Redistribution and use in source and binary forms, with or without "
        "modification... may be used to endorse or promote products derived "
        "from this software without specific prior written permission."
    )
    assert classify_license_text(bsd3_text) == "BSD-3-Clause"
    assert _classify_license_text(bsd3_text) == "BSD-3-Clause"


def test_classify_truncated_bsd3_as_bsd2_best_effort() -> None:
    truncated_bsd3 = (
        Path(__file__).parent / "fixtures" / "truncated-bsd3.txt"
    ).read_text(encoding="utf-8")

    assert "endorse or promote products derived" not in truncated_bsd3.lower()
    assert classify_license_text(truncated_bsd3) == "BSD-2-Clause"
    assert _classify_license_text(truncated_bsd3) == "BSD-2-Clause"


def test_resolve_go_license_reads_cached_module(tmp_path: Path) -> None:
    gomodcache = tmp_path / "gomodcache"
    mod_dir = gomodcache / "github.com/!example/pkg@v1.2.3"
    mod_dir.mkdir(parents=True)
    (mod_dir / "LICENSE").write_text(
        "Permission is hereby granted, free of charge, to any person...",
        encoding="utf-8",
    )
    lic = resolve_go_license("github.com/Example/pkg", "v1.2.3", gomodcache=gomodcache)
    assert lic == "MIT"


def test_resolve_go_license_missing_module_is_unknown(tmp_path: Path) -> None:
    gomodcache = tmp_path / "gomodcache"
    gomodcache.mkdir()
    lic = resolve_go_license("github.com/nope/pkg", "v0.0.1", gomodcache=gomodcache)
    assert lic == UNKNOWN_LICENSE


def test_scan_go_licenses_from_go_sum(tmp_path: Path) -> None:
    gomodcache = tmp_path / "gomodcache"
    mod_dir = gomodcache / "golang.org/x/net@v0.17.0"
    mod_dir.mkdir(parents=True)
    (mod_dir / "LICENSE").write_text(
        "Redistribution and use in source and binary forms, with or without "
        "modification... may be used to endorse or promote products derived "
        "from this software without specific prior written permission.",
        encoding="utf-8",
    )
    (tmp_path / "go.sum").write_text(
        "golang.org/x/net v0.17.0 h1:abc=\n"
        "golang.org/x/net v0.17.0/go.mod h1:xyz=\n"
        "golang.org/x/unknown v0.0.1 h1:def=\n",
        encoding="utf-8",
    )
    results = scan_go_licenses(tmp_path, gomodcache=gomodcache)
    by_name = {r.name: r for r in results}
    assert len(results) == 2  # /go.mod line collapses onto the same entry
    assert by_name["golang.org/x/net"].license_id == "BSD-3-Clause"
    assert by_name["golang.org/x/net"].source == "go.sum+GOMODCACHE"
    assert by_name["golang.org/x/unknown"].license_id == UNKNOWN_LICENSE
    assert all(r.ecosystem == "go" for r in results)


# ---------------------------------------------------------------------------
# Rust: scan_rust_licenses (cargo metadata mocked; no real subprocess)
# ---------------------------------------------------------------------------


def _cargo_metadata_json(packages: list[dict[str, object]]) -> str:
    return json.dumps({"packages": packages})


def test_scan_rust_licenses_via_mocked_cargo_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"\n', encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("# fake lock\n", encoding="utf-8")

    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = _cargo_metadata_json(
        [
            {
                "name": "app",
                "version": "0.1.0",
                "license": None,
                "source": None,  # workspace-local -> excluded
            },
            {
                "name": "serde",
                "version": "1.0.0",
                "license": "MIT OR Apache-2.0",
                "source": "registry+https://github.com/rust-lang/crates.io-index",
            },
            {
                "name": "no-license-crate",
                "version": "2.0.0",
                "license": None,
                "source": "registry+https://github.com/rust-lang/crates.io-index",
            },
        ]
    )

    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/cargo")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)

    results = scan_rust_licenses(tmp_path)
    by_name = {r.name: r for r in results}
    assert set(by_name) == {"serde", "no-license-crate"}  # workspace member excluded
    assert by_name["serde"].license_id == "MIT OR Apache-2.0"
    assert by_name["no-license-crate"].license_id == UNKNOWN_LICENSE
    assert all(r.ecosystem == "rust" for r in results)


def test_scan_rust_licenses_skips_gracefully_without_cargo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"\n', encoding="utf-8")
    (tmp_path / "Cargo.lock").write_text("# fake lock\n", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert scan_rust_licenses(tmp_path) == []


# ---------------------------------------------------------------------------
# Deno: resolve_deno_npm_license / scan_deno_licenses (DENO_DIR fixture cache)
# ---------------------------------------------------------------------------


def _write_deno_npm_cache_pkg(
    deno_dir: Path, name: str, version: str, lic: str
) -> None:
    pkg_dir = deno_dir / "npm" / "registry.npmjs.org" / name / version
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": name, "version": version, "license": lic}),
        encoding="utf-8",
    )


def test_resolve_deno_npm_license_from_cache(tmp_path: Path) -> None:
    deno_dir = tmp_path / "denodir"
    _write_deno_npm_cache_pkg(deno_dir, "lodash", "4.17.21", "MIT")
    assert resolve_deno_npm_license("lodash", "4.17.21", deno_dir=deno_dir) == "MIT"


def test_resolve_deno_npm_license_missing_is_unknown(tmp_path: Path) -> None:
    deno_dir = tmp_path / "denodir"
    deno_dir.mkdir()
    assert (
        resolve_deno_npm_license("nope", "1.0.0", deno_dir=deno_dir) == UNKNOWN_LICENSE
    )


def test_scan_deno_licenses_npm_specifiers_only(tmp_path: Path) -> None:
    deno_dir = tmp_path / "denodir"
    _write_deno_npm_cache_pkg(deno_dir, "chalk", "5.3.0", "MIT")
    lockfile = tmp_path / "deno.lock"
    lockfile.write_text(
        json.dumps(
            {
                "version": "3",
                "packages": {
                    "npm": {"chalk@5.3.0": {"integrity": "sha512-x"}},
                },
            }
        ),
        encoding="utf-8",
    )
    results = scan_deno_licenses(tmp_path, deno_dir=deno_dir)
    assert len(results) == 1
    assert results[0].name == "chalk"
    assert results[0].version == "5.3.0"
    assert results[0].license_id == "MIT"
    assert results[0].ecosystem == "deno"
    assert results[0].source == "deno.lock+DENO_DIR"


def test_enrich_license_uses_cached_npm_registry_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "license-cache"
    url = "https://registry.npmjs.org/lodash/4.17.21"
    cache_file = _cache_path(cache, url)
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(
        json.dumps({"name": "lodash", "version": "4.17.21", "license": "MIT"}),
        encoding="utf-8",
    )

    def network_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("enrichment should use the cached response")

    monkeypatch.setattr("urllib.request.urlopen", network_must_not_run)
    assert (
        enrich_license(
            "lodash",
            "4.17.21",
            "npm",
            cache_dir=cache,
            allow_network=False,
        )
        == "MIT"
    )
    assert (
        enrich_license(
            "lodash",
            "4.17.21",
            "deno",
            cache_dir=cache,
            allow_network=False,
        )
        == "MIT"
    )


def test_enrich_license_reads_python_and_cargo_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site_packages = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
    dist_info = site_packages / "requests-2.31.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: requests\nVersion: 2.31.0\nLicense: Apache-2.0\n",
        encoding="utf-8",
    )
    assert (
        enrich_license("requests", "2.31.0", "pypi", tmp_path, allow_network=False)
        == "Apache-2.0"
    )

    cargo_home = tmp_path / "cargo"
    crate = cargo_home / "registry" / "src" / "index" / "serde-1.0.0"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "serde"\nversion = "1.0.0"\nlicense = "MIT OR Apache-2.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CARGO_HOME", str(cargo_home))
    assert (
        enrich_license(
            "serde",
            "1.0.0",
            "cargo",
            cache_dir=tmp_path,
            allow_network=False,
        )
        == "MIT OR Apache-2.0"
    )


def test_normalize_spdx_rejects_ambiguous_values() -> None:
    assert normalize_spdx("MIT License") == "MIT"
    assert normalize_spdx("MIT OR Apache-2.0") == "MIT OR Apache-2.0"
    assert normalize_spdx("BSD") is None


# ---------------------------------------------------------------------------
# Unknown-exclusion gate: all scanned ecosystems participate in --fail-unknown
# ---------------------------------------------------------------------------


def test_fail_gate_unknown_passes_through_all_ecosystems() -> None:
    unknown = [
        PackageLicense(
            name="mystery-lib",
            version="1.0.0",
            license_id=UNKNOWN_LICENSE,
            ecosystem="npm",
            source="node_modules",
        ),
        PackageLicense(
            name="mystery-py",
            version="2.0.0",
            license_id=UNKNOWN_LICENSE,
            ecosystem="python",
            source="uv.lock",
        ),
    ]
    gated = _fail_gate_unknown(unknown)
    assert len(gated) == 2
    assert {p.name for p in gated} == {"mystery-lib", "mystery-py"}


def test_fail_gate_unknown_empty_when_no_unknown() -> None:
    assert _fail_gate_unknown([]) == []
