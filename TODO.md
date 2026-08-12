# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for claims `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

---

## Round 30 — Wave 30 audit carry-forwards

### 30.1 Fleet config e2e: Yarn / Bun / Deno manager paths

`tests/test_config_e2e.py` fleet harness covers pnpm, npm, container, pre-commit, and submodule per-checkout readers. Unit coverage for yarn/bun/pnpm update-latest/targeted-bump and npm targeted-bump lives in `tests/test_js_manager_checkout_flags.py` (mocked `load_repo_config`). Deno still has direct helper coverage only. Fleet-path e2e for yarn/bun/deno remains deferred.

**Refs:** `tests/test_config_e2e.py`; `tests/test_js_manager_checkout_flags.py`; `repo_ops_js_yarn.yarn`; `repo_ops_js_bun.bun`; `repo_ops_js.deno`.

**Acceptance:** Fleet e2e asserts divergent per-checkout resolution for yarn and bun update-latest/targeted-bump (and deno via fleet path, not only direct helpers).

---

## Round 36 — Wave 36 audit carry-forwards

### 36.2 Bun integration: targeted-bump releases unheld pin

`tests/integration/test_bun_ghost_bump.py` covers raise-without-targeted-bump only. Yarn has `test_yarn_apply_targeted_bump_releases_unheld_pin_instead_of_raising`. Unit coverage for bun defer/release landed in wave36; fleet/integration path still open.

**Refs:** `tests/integration/test_bun_ghost_bump.py`; `tests/integration/test_yarn_ghost_bump.py`.

**Acceptance:** Integration test: bun apply with targeted-bump releases an unheld overrides pin and advances instead of raising.

---

## Round 39 — Upgrade-mode and discovery gaps

### 39.1 Python managers: no `--latest` / targeted-bump

`--full` / `--latest` plus `--*-targeted-bump` close JS/Deno `behind_latest` gaps. poetry, pdm, pipenv, and pip-tools have no equivalent: `poetry update` / `pdm update` / `pipenv update` stay inside existing constraints, and usage.md marks pdm/pipenv aggressive mode as “—”. uv already runs `uv lock --upgrade` (constraint-respecting, not a JS-style latest umbrella). Operators on Poetry/PDM fleets therefore under-report the same class of drift `--full` is meant to catch.

**Refs:** `docs/usage.md` § Ecosystem upgrade modes; `packages/repo-ops-py-poetry/src/repo_ops_py_poetry/poetry.py`; `packages/repo-ops-py-pdm/src/repo_ops_py_pdm/pdm.py`; `packages/repo-ops-py-pipenv/src/repo_ops_py_pipenv/pipenv.py`; `packages/repo-ops-py-pip-tools/src/repo_ops_py_pip_tools/pip_tools.py`; `packages/repo-ops-core/src/repo_ops_core/cli.py` (flag wiring for JS targeted-bump).

**Traps:** Do not silently rewrite `^1.2` to `^2` in `pyproject.toml` without an opt-in flag (same contract as `--yarn-targeted-bump`). pip-tools `requirements.in` pins are the constraint — a “latest” mode must be explicit. pipenv must not create a venv (existing check is read-only via `pipenv --venv`). Hold clamp is lock-only (uv `lock --upgrade-package`; poetry/pdm/pipenv snapshot-restore the manifest) and must apply to any new bump path.

**Acceptance:** Opt-in flags (or a documented “Python has no latest mode” sentence that is true for all four) exist for poetry/pdm/pipenv/pip-tools. With the flag, a direct dep whose lock is behind a newer published version that requires a constraint bump is either moved (revert-on-failure, like JS targeted-bump) or listed in `behind_latest` instead of `[ok]`. `--full` either includes those flags or the docs say Python constraint bumps stay manual.

### 39.2 pnpm `catalog:` versions in `pnpm-workspace.yaml`

Overrides audit treats `catalog:` protocol pins as non-stale (`repo_ops_js.overrides`, #50). `repo-ops-js-pnpm` never reads or bumps the `catalogs:` / `catalog:` tables in `pnpm-workspace.yaml`. `pnpm update --latest` can leave catalog-defined versions untouched while member `package.json` still says `catalog:`. Fleet repos using pnpm catalogs then look current in plan/apply.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/overrides.py` (`catalog:` skip); `packages/repo-ops-js-pnpm/src/repo_ops_js_pnpm/`; pnpm workspace catalog docs.

**Traps:** Default vs named catalogs (`catalog:` vs `catalog:foo`). Do not rewrite `workspace:` / `link:` / `file:` siblings. Catalog bumps are workspace-root files — `apply --commit` scoping must include `pnpm-workspace.yaml` (same class of bug as #153 nested manifests). Holds must apply to catalog package names.

**Acceptance:** Plan/apply can advance a catalog entry when `--pnpm-targeted-bump` / `--latest` says to, with snapshot/restore if `pnpm install` fails. Catalog-only drift shows in `behind_latest` when not actioned. `pnpm-workspace.yaml` is in the commit path when the catalog table changed.

### 39.3 Opt-in Cargo.toml constraint bump (Rust majors still default-off)

`cargo update` only refreshes within existing `Cargo.toml` reqs; `--latest` is documented as a no-op for rust. Direct-dep major/minor constraint edits stay hand work. That is honest, but there is still no opt-in analog to `--yarn-targeted-bump` (e.g. `cargo update -p crate --precise` already used for holds/audit, plus a manifest req bump).

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py`; `docs/usage.md` rust row; `AGENTS.md` (`cargo update` only); `packages/repo-ops-audit` cargo `--precise` path.

**Traps:** Default must remain “no Cargo.toml edits.” Do not take a hard dependency on `cargo-edit` if `cargo update --precise` + a small toml edit suffices. Workspace inheritance (`workspace.dependencies`) and path/git crates must be skipped. Yanked crates.io versions (already fetched in `_cargo_registry_versions`).

**Acceptance:** Without a new flag, rust behavior is unchanged. With the flag, a direct crates.io dep whose latest published version is outside the current req can be bumped in `Cargo.toml` + lock, reverted on `cargo update` failure, and listed in `behind_latest` when skipped. Path/git/workspace crates never get a registry bump.

---

## Round 41 — Wave 41 audit carry-forwards

### 41.1 Go hold ceiling still duplicates pad-to-three

Python managers call `repo_ops_core.version_utils.hold_ceiling_spec`. `packages/repo-ops-go/src/repo_ops_go/go.py` still has `_HOLD_CEILING_PAD` and `_go_ceiling_query` (`<=v` + same three-component pad).

**Refs:** `go.py` `_go_ceiling_query`; `version_utils.hold_ceiling_spec`.

**Traps:** Go queries need the `v` prefix (`<=v6.999999.999999`). Do not change Python `<=` PEP 440 shape.

**Acceptance:** Go uses the shared pad (or a thin `v`-prefixed wrapper). Padding semantics unchanged.

### 41.2 CycloneDX tool provenance: `author` vs `publisher`

`emit_cyclonedx` maps 1.4 `vendor` to component `author: "Rethunk-Tech"`. CycloneDX 1.5+ also has `publisher` and structured `authors[]`; GitHub/OSV consumers may prefer those.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/sbom.py` `emit_cyclonedx`; CycloneDX 1.5 metadata.tools.components.

**Acceptance:** Tool component identity is schema-valid and matches how GitHub dependency-graph / common validators expect org provenance. Tests lock the chosen fields.

### 41.3 CycloneDX emit has no schema-validation test

`tests/test_sbom.py` asserts dict equality for `specVersion` and `metadata.tools`. No CI check against `bom-1.5.schema.json`.

**Refs:** `tests/test_sbom.py`; CycloneDX JSON schema.

**Traps:** Do not add `cyclonedx-python-lib` only to validate. A vendored/minimal schema fixture or a skip-if-uninstalled jsonschema check is enough.

**Acceptance:** A unit test fails if `emit_cyclonedx` output is invalid 1.5 JSON (tools shape, required keys).

### 41.4 Filesystem tomli-reload test mutates `sys.modules`

`test_tomli_import_error_marks_parser_unavailable` reloads `filesystem` and rewrites `sys.modules["tomllib"/"tomli"]`. Finally restores, but the pattern is brittle under parallel collection.

**Refs:** `tests/test_filesystem.py`.

**Acceptance:** The ImportError fallback is still covered without leaking parser modules across tests (isolate via a subprocess or a dedicated import helper that tests can call without reload).
