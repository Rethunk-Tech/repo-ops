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

Pre-commit `[[hold]]` ceilings are wired (filter tag-rev bumps and SHA-refresh targets; expired holds ignored; no post-apply clamp yet). Still unwired: container, GitHub Actions, submodules. Rust and Deno remain outside the enforcement matrix.

**Refs:** `docs/usage.md` § Package holds; `repo_ops_core.holds`; checkers in `packages/repo-ops-container`, `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py`, `submodules.py`; `packages/repo-ops-rust`, `packages/repo-ops-js/src/repo_ops_js/deno.py`; pre-commit reference: `precommit.py` + `tests/test_precommit_holds.py`.

**Traps:** Actions/container “versions” are often digests or floating majors (`v4` vs SHA); a semver ceiling must define digest-refresh / unpin behaviour explicitly — including interaction with `--submodule-refresh-sha` / Actions/container refresh. Audit `--fix` advisory overrides must continue to win over holds (parity with JS/Python). Go already documents same-major-only ceilings — do not invent cross-major Go holds.

**Acceptance:** A hold on an Actions pin, container tag/digest, or submodule ref caps plan/apply the same way JS/Go/Python/pre-commit do; docs list each ecosystem; expired holds still skip without rewriting config.

### 1.3 Pre-commit hold post-apply clamp

Pre-commit holds filter outbound check/apply Changes only. A rev already sitting above a newly added ceiling is not rewritten back to the ceiling on apply.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py`; JS/Go `enforce_holds` / `_enforce_version_ceilings` for the clamp pattern; `docs/usage.md` pre-commit hold bullet.

**Acceptance:** Apply clamps an above-ceiling tag (or resolvable SHA) rev down to the hold ceiling when safe; docs drop the “not clamped post-apply yet” caveat.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: remaining per-checkout config keys

Fleet `plan`/`apply` now resolve per checkout: exclusions, Actions lint/policy levels, commit-agent model/provider, and `go_upgrade_mode` (via ambient-env snapshot so injected global config cannot beat per-repo). Other documented keys (`jobs`, timeouts, yarn concurrency, verify profile wiring beyond existing `verify.py` path, `*_update_latest` / `*_targeted_bump`, etc.) remain invocation/global-scoped.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`_ambient_env`, per-bundle exclude / actions resolve); `repo_ops_core.config` (`set_ambient_env` / `ambient_env` / `resolve_setting`); `packages/repo-ops-go/src/repo_ops_go/go.py` (`_go_upgrade_mode`); `docs/usage.md` config section; `tests/test_config_e2e.py`.

**Traps:** Do not mutate process-wide env per checkout mid-flight. Prefer resolve-at-use with ambient snapshot over `apply_config_to_env` swaps. `jobs` / yarn concurrency are process-global thread-pool knobs — do not pretend they are per-checkout without an architecture change.

**Acceptance:** Remaining documented repo-scoped settings honor each checkout’s config on fleet runs; CLI + caller env precedence unchanged; docs list which keys are per-checkout vs invocation-scoped.

---

## Round 25 — Wave 25 follow-ons

### 25.1 Network-only enrich: assert `allow_network=True` at call site

`test_network_only_enrichment_for_license_and_sbom` proves enrichment via network side effects / SBOM output but does not spy `enrich_license(..., allow_network=True)`.

**Refs:** `tests/test_license_policy.py`; `license_enrich.enrich_license`.

**Acceptance:** Regression patches `enrich_license` (or equivalent) and asserts `allow_network is True` on the network-only flag path.

### 25.2 Fleet go mode E2E via `check_go` path

`test_fleet_run_resolves_go_upgrade_mode_per_checkout` observes `_go_upgrade_mode` directly from a patched `process_workspace` rather than mode selection inside `check_go`.

**Refs:** `tests/test_config_e2e.py`; `packages/repo-ops-go/src/repo_ops_go/go.py`.

**Acceptance:** Fleet isolation test routes through the registered go check/apply path (or otherwise proves the consumer that plan/apply actually call).

### 25.3 Ruff S112 / deno SIM117 pre-existing gate noise

`uv run ruff check .` is red on pre-wave HEAD for `try/except/continue` in SHA-refresh helpers (`precommit.py`, `submodules.py`) and a SIM117 nested-`with` in `tests/test_deno_check.py`. Wave 25 did not introduce these; import-sort I001s from the wave were fixed.

**Acceptance:** `uv run ruff check .` exits 0 on a clean tree (fix or justified narrow pattern matching the existing `# noqa: S112` sites — prefer logging over suppressions when adding new ones).
