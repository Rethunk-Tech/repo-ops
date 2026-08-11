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

Rust and Deno `[[hold]]` ceilings are wired (filter, post-apply clamp, expired holds, prefix-ceiling registry/range resolve). Still deferred: pin-ecosystem audit `--fix` advisory overrides that lift a hold for a remediation (parity with JS/Python `_AUDIT_VERSION_OVERRIDES` paths across npm/pnpm/yarn/bun + audit orchestration).

**Refs:** `docs/usage.md` § Package holds; `repo_ops_core.holds`; JS `audit_override` / `set_*_audit_version_overrides` in `repo_ops_js_*`; `repo_ops_audit.audit.apply_remediations`.

**Acceptance:** Audit `--fix` wins over holds where an override path exists; docs list each ecosystem’s override behavior.

---

## Round 9 — Wave 17 / 29 follow-ons

### 9.1 Fleet apply: remaining per-checkout config keys

Fleet `plan`/`apply` now resolve per checkout: exclusions, Actions lint/policy levels, commit-agent model/provider, `go_upgrade_mode`, `go_work_sync`, `go_directive`, `go_toolchain`, timeouts (`timeout_check` / `timeout_apply` / `timeout_verify` / `find_timeout` / `git_timeout`), and `node_verify_script` (via ambient-env snapshot + resolve-at-use). Still invocation/global-scoped: `jobs`, `yarn_max_concurrency`, `*_update_latest`, `*_targeted_bump`, refresh-sha family, and other flags listed in `docs/usage.md` as global.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py`; `repo_ops_core.config`; `repo_ops_core.constants`; `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py`; `docs/usage.md` config section; `tests/test_config_e2e.py`.

**Traps:** Do not mutate process-wide env per checkout mid-flight. Prefer resolve-at-use with ambient snapshot over `apply_config_to_env` swaps. `jobs` / yarn concurrency are process-global thread-pool knobs — do not pretend they are per-checkout without an architecture change. Global-config values injected into `os.environ` must not be misclassified as CLI overrides (see `_process_env_cli_override` in `go.py`). Root-level `find` discovery runs before checkout configs are known, so its timeout fallback stays global until per-checkout processing begins.

**Acceptance:** Remaining documented repo-scoped settings (`*_update_latest` / `*_targeted_bump` / refresh-sha family, and any other keys still marked global that should be per-checkout) honor each checkout’s config on fleet runs; CLI + caller env precedence unchanged; docs list which keys are per-checkout vs invocation-scoped.

---

## Round 29 — Wave 29 audit carry-forwards

### 29.1 Deno hold clamp: explicit import-map path

Hold clamp runs `deno update` without an explicit `--import-map`. Deno often auto-discovers `deno.json`, but import-map-only layouts (or non-default map paths) may need the same map path already used elsewhere in `deno.py` when lock-only update is insufficient.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/deno.py` (`_enforce_deno_version_ceilings`); Deno `update` / import-map docs.

**Acceptance:** Clamp lands within-ceiling versions for import-map-backed `npm:` / `jsr:` deps without relying solely on default auto-discovery.
