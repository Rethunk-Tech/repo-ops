# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

### 1.2 Package holds residuals (Rust / Deno / audit override)

Actions, container, and submodule `[[hold]]` ceilings are wired (filter, plan clamp visibility, post-apply clamp, expired holds). Still unwired: Rust and Deno. Pin-ecosystem audit `--fix` advisory overrides (parity with JS/Python) remain deferred.

**Refs:** `docs/usage.md` § Package holds; `repo_ops_core.holds`; `packages/repo-ops-rust`; `packages/repo-ops-js/src/repo_ops_js/deno.py`; JS `audit_override` pattern in `repo_ops_js`.

**Acceptance:** Rust/Deno holds match other ecosystems; audit `--fix` wins over holds where an override path exists; docs list each.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: remaining per-checkout config keys

Fleet `plan`/`apply` now resolve per checkout: exclusions, Actions lint/policy levels, commit-agent model/provider, `go_upgrade_mode`, and `go_work_sync` (via ambient-env snapshot). Other documented keys (`jobs`, timeouts, yarn concurrency, verify profile wiring beyond existing `verify.py` path, `*_update_latest` / `*_targeted_bump`, etc.) remain invocation/global-scoped.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py`; `repo_ops_core.config`; `packages/repo-ops-go/src/repo_ops_go/go.py`; `docs/usage.md` config section; `tests/test_config_e2e.py`.

**Traps:** Do not mutate process-wide env per checkout mid-flight. Prefer resolve-at-use with ambient snapshot over `apply_config_to_env` swaps. `jobs` / yarn concurrency are process-global thread-pool knobs — do not pretend they are per-checkout without an architecture change.

**Acceptance:** Remaining documented repo-scoped settings honor each checkout’s config on fleet runs; CLI + caller env precedence unchanged; docs list which keys are per-checkout vs invocation-scoped.

---

## Round 27 — Wave 27 audit carry-forwards

### 27.1 `go_work_sync` global-config-only precedence test

`_go_work_sync_enabled` mirrors `_go_upgrade_mode` (repo / ambient / CLI). Tests cover those three; missing coverage for `global_cfg`-only enablement with empty repo config and empty ambient.

**Refs:** `packages/repo-ops-go/src/repo_ops_go/go.py` (`_go_work_sync_enabled`); `tests/test_go_env_classify.py`.

**Acceptance:** A focused test proves global config alone enables `go_work_sync` when repo/ambient/CLI are unset.
