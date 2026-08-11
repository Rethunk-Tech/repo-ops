# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

### 1.2 Package holds residuals (audit `--fix` override)

Rust and Deno `[[hold]]` ceilings are wired (filter, post-apply clamp, expired holds). Still deferred: pin-ecosystem audit `--fix` advisory overrides that lift a hold for a remediation (parity with JS/Python `_AUDIT_VERSION_OVERRIDES` paths across npm/pnpm/yarn/bun + audit orchestration).

**Refs:** `docs/usage.md` § Package holds; `repo_ops_core.holds`; JS `audit_override` / `set_*_audit_version_overrides` in `repo_ops_js_*`; `repo_ops_audit.audit.apply_remediations`.

**Acceptance:** Audit `--fix` wins over holds where an override path exists; docs list each ecosystem’s override behavior.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: remaining per-checkout config keys

Fleet `plan`/`apply` now resolve per checkout: exclusions, Actions lint/policy levels, commit-agent model/provider, `go_upgrade_mode`, `go_work_sync`, `go_directive`, and `go_toolchain` (via ambient-env snapshot + resolve-at-use). Other documented keys (`jobs`, timeouts, yarn concurrency, `node_verify_script`, `*_update_latest` / `*_targeted_bump` / refresh-sha family, etc.) remain invocation/global-scoped.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py`; `repo_ops_core.config`; `packages/repo-ops-go/src/repo_ops_go/go.py`; `docs/usage.md` config section; `tests/test_config_e2e.py`.

**Traps:** Do not mutate process-wide env per checkout mid-flight. Prefer resolve-at-use with ambient snapshot over `apply_config_to_env` swaps. `jobs` / yarn concurrency are process-global thread-pool knobs — do not pretend they are per-checkout without an architecture change. Global-config values injected into `os.environ` must not be misclassified as CLI overrides (see `_process_env_cli_override` in `go.py`).

**Acceptance:** Remaining documented repo-scoped settings honor each checkout’s config on fleet runs; CLI + caller env precedence unchanged; docs list which keys are per-checkout vs invocation-scoped.

---

## Round 28 — Wave 28 audit carry-forwards

### 28.1 Rust hold prefix ceilings without registry candidates

Cargo `--precise` needs an exact published version. Prefix holds (`max_version = "1.5"`) currently zero-pad to `1.5.0` when no in-metadata candidate already satisfies the ceiling. Crates that never published `1.5.0` (only `1.5.x` with x>0) still fail the clamp loudly.

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py` (`_cargo_precise_version`, `_enforce_version_ceilings`); `docs/usage.md` § Package holds rust bullet.

**Acceptance:** Clamp picks the highest registry version within the prefix ceiling (or documents that Rust holds should use exact three-component `max_version` when no candidate is known).

### 28.2 Deno hold clamp via import-map / range semantics

Deno clamp now targets `name@<normalized-ceiling>` (e.g. `1.5` → `1.5.0`). Confirm whether Deno accepts range specs (`@<=1.5`) for `deno update` the way npm does, and whether import-map rewrites need an explicit path when lock-only updates are insufficient.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/deno.py` (`_enforce_deno_version_ceilings`, `_deno_version_target`); Deno `update` / `outdated` docs.

**Acceptance:** Clamp reliably lands a within-ceiling version for both npm: and jsr: identities without requiring the exact `.0` patch to exist.
