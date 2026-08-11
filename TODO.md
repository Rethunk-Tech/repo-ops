# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

---

## Round 30 — Wave 30 audit carry-forwards

### 30.1 Fleet config e2e: Yarn / Bun / Deno manager paths

`tests/test_config_e2e.py` fleet harness covers pnpm, npm, container, pre-commit, and submodule per-checkout readers. Wave 31 added **unit** coverage for yarn/bun per-checkout update-latest/targeted-bump in `tests/test_js_manager_checkout_flags.py`; Deno still has direct helper coverage only. Fleet-path e2e for yarn/bun/deno remains deferred.

**Refs:** `tests/test_config_e2e.py`; `tests/test_js_manager_checkout_flags.py`; `repo_ops_js_yarn.yarn`; `repo_ops_js_bun.bun`; `repo_ops_js.deno`.

**Acceptance:** Fleet e2e asserts divergent per-checkout resolution for yarn and bun update-latest/targeted-bump (and deno via fleet path, not only direct helpers).

---

## Round 31 — Wave 31 audit carry-forwards

### 31.1 `test_container_refresh_sha_from_config_file` still bypasses TOML load

Wave 31 rewrote precommit/submodule refresh-sha tests to load a real `config.toml` via `load_global_config()`. The older sibling `test_container_refresh_sha_from_config_file` still injects an in-memory dict into `apply_config_to_env` and leaves `tmp_path` unused — same false-green pattern.

**Refs:** `tests/test_config.py` (`test_container_refresh_sha_from_config_file`); pattern to copy: `test_precommit_refresh_sha_from_config_file`.

**Acceptance:** Container refresh-sha test writes/reads a real global `config.toml` through `load_global_config()` before `apply_config_to_env`, matching the precommit/submodule siblings.
