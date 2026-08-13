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

### 39.3 Opt-in Cargo.toml constraint bump (Rust majors still default-off)

`cargo update` only refreshes within existing `Cargo.toml` reqs; `--latest` is documented as a no-op for rust. Direct-dep major/minor constraint edits stay hand work. That is honest, but there is still no opt-in analog to `--yarn-targeted-bump` (e.g. `cargo update -p crate --precise` already used for holds/audit, plus a manifest req bump).

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py`; `docs/usage.md` rust row; `AGENTS.md` (`cargo update` only); `packages/repo-ops-audit` cargo `--precise` path.

**Traps:** Default must remain “no Cargo.toml edits.” Do not take a hard dependency on `cargo-edit` if `cargo update --precise` + a small toml edit suffices. Workspace inheritance (`workspace.dependencies`) and path/git crates must be skipped. Yanked crates.io versions (already fetched in `_cargo_registry_versions`).

**Acceptance:** Without a new flag, rust behavior is unchanged. With the flag, a direct crates.io dep whose latest published version is outside the current req can be bumped in `Cargo.toml` + lock, reverted on `cargo update` failure, and listed in `behind_latest` when skipped. Path/git/workspace crates never get a registry bump.

---

## Round 42 — Wave 42 audit carry-forwards

### 42.1 Catalog drift invisible when absent from `pnpm outdated`

`_pnpm_outdated` skips a catalog package when `pnpm outdated` JSON has no row (`if meta is None: continue`). A stale `pnpm-workspace.yaml` pin with no outdated entry never appears in `behind_latest`.

**Refs:** `packages/repo-ops-js-pnpm/src/repo_ops_js_pnpm/pnpm.py` `_pnpm_outdated` catalog merge.

**Traps:** Do not hit the registry on every plan. Skip protocol values. Holds still apply.

**Acceptance:** Catalog-only constraints that are behind a known latest (from outdated **or** an existing in-tree latest helper) show in `behind_latest` when not actioned.

### 42.2 `behind_latest` catalog `from_ver` is the YAML spec

Catalog `Change.from_ver` is the workspace-yaml constraint (`^18.2.0`), not the installed `current` from outdated. Other managers report the resolved version.

**Refs:** `pnpm.py` catalog `behind_latest.append`; `tests/test_pnpm_catalog.py`.

**Acceptance:** Human/JSON `behind_latest` for catalog packages uses the same `from_ver` convention as non-catalog pnpm rows, or docs state the spec-as-from_ver rule.

### 42.3 Commit subject ignores `pnpm-workspace.yaml`

`upgrade_scoped_paths` stages `pnpm-workspace.yaml` for pnpm. `classify_commit_subject` `has_node` does not include that basename, so a catalog-only commit gets the generic deps subject.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/post_apply_commit.py` `classify_commit_subject`.

**Acceptance:** A commit whose only node file is `pnpm-workspace.yaml` classifies as a node/pnpm deps subject.

### 42.4 Go ceiling docstring still describes local padding

`_go_ceiling_query` delegates to `hold_ceiling_spec`; the docstring still narrates in-function three-component padding.

**Refs:** `packages/repo-ops-go/src/repo_ops_go/go.py` `_go_ceiling_query`.

**Acceptance:** Docstring states the `v`-prefixed wrapper over `hold_ceiling_spec` without implying a local pad loop.

### 42.5 `TOML_AVAILABLE=False` at module init untested

`load_toml_parser(importer=broken)` is covered. Module-level `TOML_AVAILABLE` after both parsers fail at import is not.

**Refs:** `packages/repo-ops-core/src/repo_ops_core/filesystem.py`; `tests/test_filesystem.py`.

**Traps:** Do not `importlib.reload` or rewrite `sys.modules`.

**Acceptance:** Init-time parser-absent path is covered without cross-test module leaks.
