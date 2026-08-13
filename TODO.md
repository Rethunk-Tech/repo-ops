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

### 39.1 pipenv / pip-tools: no targeted-bump

Poetry and PDM have `--poetry-targeted-bump` / `--pdm-targeted-bump` (not in `--full`). pipenv and pip-tools still stay inside existing constraints: `pipenv update` and pip-tools compile from `requirements.in` with no constraint-rewrite path. uv remains `uv lock --upgrade` (constraint-respecting).

**Refs:** `packages/repo-ops-py-pipenv/src/repo_ops_py_pipenv/pipenv.py`; `packages/repo-ops-py-pip-tools/src/repo_ops_py_pip_tools/pip_tools.py`; `packages/repo-ops-core/src/repo_ops_core/cli.py`; `docs/usage.md` § Ecosystem upgrade modes.

**Traps:** Do not silently rewrite `requirements.in` pins or Pipfile specs without an opt-in flag. pipenv must not create a venv (existing check is read-only via `pipenv --venv`). Hold clamp must apply to any new bump path.

**Acceptance:** Opt-in flags exist for pipenv and pip-tools, or usage.md states those two stay manual. With the flag, a direct dep whose lock is behind a newer published version that requires a constraint bump is either moved (revert-on-failure) or listed in `behind_latest`. `--full` does not include them unless docs say otherwise.

---

## Round 44 — Wave 1 audit carry-forwards

### 44.1 Shared requirement parser

Rust, poetry, and pdm each ship a ~70-line caret/range parser (`_cargo_requirement_satisfied`, `_poetry_requirement_satisfied`, `_pdm_requirement_satisfied`). Default-operator drift (`^` vs `=`) already happened once.

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py`; `packages/repo-ops-py-poetry/src/repo_ops_py_poetry/poetry.py`; `packages/repo-ops-py-pdm/src/repo_ops_py_pdm/pdm.py`.

**Acceptance:** One shared helper (or documented why Cargo vs PEP 440 must stay split) with a single default-operator contract and tests.

### 44.2 Poetry/PDM `from_ver` is the constraint, not lock current

Targeted-bump `Change.from_ver` is the pyproject requirement string so apply can preserve `^`/`~`. Plan output therefore does not match pnpm's lock-resolved `current`.

**Refs:** `poetry.py` `_poetry_latest_direct_changes`; `pdm.py` `_pdm_latest_direct_changes`.

**Traps:** Apply still needs the constraint string; do not replace `from_ver` without a second field.

**Acceptance:** Plan JSON/human output shows lock-resolved current (and separately the constraint if needed) without breaking apply prefix preservation.

### 44.3 Rust cache key vs concurrent manifest write

`_cargo_latest_direct_changes` stats Cargo.toml then `@cache`s on `(path, mtime_ns, size)`. A same-size rewrite in the same nanosecond can associate a stale key with new bytes.

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py` `_cargo_latest_direct_changes`.

**Acceptance:** Cache key includes content digest, or apply does not rely on mtime uniqueness.

### 44.4 pnpm catalog lock `from_ver` prefix match

`_catalog_lock_from_ver` uses `token.startswith(name + "@")` rather than an exact name split (`license_policy._split_nv`). No false-positive repro at HEAD.

**Refs:** `packages/repo-ops-js-pnpm/src/repo_ops_js_pnpm/pnpm.py` `_catalog_lock_from_ver`.

**Acceptance:** Resolved version is taken only from tokens whose package name equals `name`.
