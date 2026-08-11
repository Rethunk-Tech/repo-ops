# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

### 1.2 Package holds for remaining pin ecosystems

Pre-commit `[[hold]]` ceilings are wired (filter outbound bumps, SHA-refresh targets, and post-apply clamp with loud failure when clamp cannot land; expired holds ignored). Still unwired: container, GitHub Actions, submodules. Rust and Deno remain outside the enforcement matrix.

**Refs:** `docs/usage.md` § Package holds; `repo_ops_core.holds`; checkers in `packages/repo-ops-container`, `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py`, `submodules.py`; `packages/repo-ops-rust`, `packages/repo-ops-js/src/repo_ops_js/deno.py`; pre-commit reference: `precommit.py` + `tests/test_precommit_holds.py`.

**Traps:** Actions/container “versions” are often digests or floating majors (`v4` vs SHA); a semver ceiling must define digest-refresh / unpin behaviour explicitly — including interaction with `--submodule-refresh-sha` / Actions/container refresh. Audit `--fix` advisory overrides must continue to win over holds (parity with JS/Python). Go already documents same-major-only ceilings — do not invent cross-major Go holds.

**Acceptance:** A hold on an Actions pin, container tag/digest, or submodule ref caps plan/apply the same way JS/Go/Python/pre-commit do; docs list each ecosystem; expired holds still skip without rewriting config.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: remaining per-checkout config keys

Fleet `plan`/`apply` now resolve per checkout: exclusions, Actions lint/policy levels, commit-agent model/provider, and `go_upgrade_mode` (via ambient-env snapshot so injected global config cannot beat per-repo). Other documented keys (`jobs`, timeouts, yarn concurrency, verify profile wiring beyond existing `verify.py` path, `*_update_latest` / `*_targeted_bump`, etc.) remain invocation/global-scoped.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`_ambient_env`, per-bundle exclude / actions resolve); `repo_ops_core.config` (`set_ambient_env` / `ambient_env` / `resolve_setting`); `packages/repo-ops-go/src/repo_ops_go/go.py` (`_go_upgrade_mode`); `docs/usage.md` config section; `tests/test_config_e2e.py`.

**Traps:** Do not mutate process-wide env per checkout mid-flight. Prefer resolve-at-use with ambient snapshot over `apply_config_to_env` swaps. `jobs` / yarn concurrency are process-global thread-pool knobs — do not pretend they are per-checkout without an architecture change.

**Acceptance:** Remaining documented repo-scoped settings honor each checkout’s config on fleet runs; CLI + caller env precedence unchanged; docs list which keys are per-checkout vs invocation-scoped.

---

## Round 26 — Wave 26 follow-ons

### 26.1 Plan visibility for impending post-apply pre-commit clamps

`check_precommit` / plan can exit clean when a rev is already at upstream latest but still above an active hold ceiling; `apply_precommit` then clamps (or raises if clamp cannot land). Matches Go/JS post-apply-only hold pattern, but plan under-reports impending rewrites.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py` (`check_precommit`, `_enforce_version_ceilings`); `docs/usage.md` Package holds.

**Acceptance:** Plan surfaces report-only or actionable entries for above-ceiling revs that apply would clamp; docs state the plan/apply asymmetry if deliberately kept.

### 26.2 Align invalid-semver log level across SHA-refresh helpers

Pre-commit SHA refresh / hold enforce logs invalid semver tags at `warning`; submodule SHA refresh logs the same condition at `debug`.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py`; `packages/repo-ops-upgrade/src/repo_ops_upgrade/submodules.py`.

**Acceptance:** Same severity for the same invalid-tag skip in both helpers (pick one level and document why if asymmetric on purpose).
