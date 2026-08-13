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

## Round 39 — Upgrade-mode and discovery gaps

### 39.1 Python managers: no `--latest` / targeted-bump

`--full` / `--latest` plus `--*-targeted-bump` close JS/Deno `behind_latest` gaps. poetry, pdm, pipenv, and pip-tools have no equivalent: `poetry update` / `pdm update` / `pipenv update` stay inside existing constraints, and usage.md marks pdm/pipenv aggressive mode as “—”. uv already runs `uv lock --upgrade` (constraint-respecting, not a JS-style latest umbrella). Operators on Poetry/PDM fleets therefore under-report the same class of drift `--full` is meant to catch.

**Refs:** `docs/usage.md` § Ecosystem upgrade modes; `packages/repo-ops-py-poetry/src/repo_ops_py_poetry/poetry.py`; `packages/repo-ops-py-pdm/src/repo_ops_py_pdm/pdm.py`; `packages/repo-ops-py-pipenv/src/repo_ops_py_pipenv/pipenv.py`; `packages/repo-ops-py-pip-tools/src/repo_ops_py_pip_tools/pip_tools.py`; `packages/repo-ops-core/src/repo_ops_core/cli.py` (flag wiring for JS targeted-bump).

**Traps:** Do not silently rewrite `^1.2` to `^2` in `pyproject.toml` without an opt-in flag (same contract as `--yarn-targeted-bump`). pip-tools `requirements.in` pins are the constraint — a “latest” mode must be explicit. pipenv must not create a venv (existing check is read-only via `pipenv --venv`). Hold clamp is lock-only (uv `lock --upgrade-package`; poetry/pdm/pipenv snapshot-restore the manifest) and must apply to any new bump path.

**Acceptance:** Opt-in flags (or a documented “Python has no latest mode” sentence that is true for all four) exist for poetry/pdm/pipenv/pip-tools. With the flag, a direct dep whose lock is behind a newer published version that requires a constraint bump is either moved (revert-on-failure, like JS targeted-bump) or listed in `behind_latest` instead of `[ok]`. `--full` either includes those flags or the docs say Python constraint bumps stay manual.

---

## Round 43 — Wave 43 audit carry-forwards

### 43.1 `apply_rust` re-queries the registry

With `--rust-targeted-bump`, `apply_rust` calls `_cargo_latest_direct_changes` again instead of reusing the plan `behind_latest` list. Extra crates.io traffic and a TOCTOU split vs `check_rust`.

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py` `apply_rust`; `check_rust`.

**Acceptance:** Apply uses the same candidate set plan already computed (or an explicit identical helper result cached on the workspace), so a no-drift plan does not hit crates.io twice.

### 43.2 Catalog missing-row `from_ver` is the stripped spec

When `pnpm outdated` has no row, catalog `behind_latest.from_ver` is `_catalog_spec_from_version` (e.g. `18.2.0` from `^18.2.0`), not lockfile-resolved current. Outdated rows already use installed `current`.

**Refs:** `packages/repo-ops-js-pnpm/src/repo_ops_js_pnpm/pnpm.py` `_pnpm_outdated` missing-meta branch; `tests/test_pnpm_catalog.py`.

**Traps:** Do not add a registry round-trip to learn current. Skip protocol values.

**Acceptance:** Missing-outdated catalog `from_ver` matches lockfile resolved version when the lock is parseable; otherwise the stripped spec remains the fallback.

### 43.3 `_catalog_spec_from_version` uses `lstrip`

`spec.lstrip("^~>=")` is prefix-character stripping, not a semver parse. Epoch pins (`1:2.3.4`) happen to survive; odd prefixes may not.

**Refs:** `pnpm.py` `_catalog_spec_from_version`; `normalize_version`.

**Acceptance:** from_ver extraction uses the same version normalizer as the rest of pnpm catalog compare, with a fixture for a non-caret prefix that `lstrip` would mangle.
