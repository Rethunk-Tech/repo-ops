# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

### 1.2 Package holds for pin ecosystems (container / Actions / pre-commit / submodules)

`[[hold]]` rules are documented as not yet consulted by container, GitHub Actions, submodule, and pre-commit checkers (same schema, no enforcement). Rust and Deno are also outside the current enforcement matrix.

**Refs:** `docs/usage.md` § Package holds (“Not yet wired…”); `repo_ops_core.holds`; checkers in `packages/repo-ops-container`, `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py`, `precommit.py`, `submodules.py`; `packages/repo-ops-rust`, `packages/repo-ops-js/src/repo_ops_js/deno.py`.

**Traps:** Actions/container “versions” are often digests or floating majors (`v4` vs SHA); a semver ceiling must define digest-refresh / unpin behaviour explicitly — including interaction with `--precommit-refresh-sha` / `--submodule-refresh-sha` / Actions/container refresh. Audit `--fix` advisory overrides must continue to win over holds (parity with JS/Python). Go already documents same-major-only ceilings — do not invent cross-major Go holds.

**Acceptance:** A hold on an Actions pin, container tag/digest, pre-commit `rev`, or submodule ref caps plan/apply the same way JS/Go/Python do; docs list each ecosystem; expired holds still skip without rewriting config.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: remaining per-checkout config keys

Fleet `plan`/`apply` now resolve per checkout: exclusions, Actions lint/policy levels, and commit-agent model/provider (via ambient-env snapshot so injected global config cannot beat per-repo). Other documented keys (`jobs`, `go_upgrade_mode`, timeouts, yarn concurrency, verify profile wiring beyond existing `verify.py` path, etc.) remain invocation/global-scoped.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`_ambient_env`, per-bundle exclude / actions resolve); `repo_ops_core.config.resolve_setting` (`env=` snapshot); `docs/usage.md` config section.

**Traps:** Do not mutate process-wide env per checkout mid-flight. Prefer resolve-at-use with ambient snapshot over `apply_config_to_env` swaps.

**Acceptance:** Remaining documented repo-scoped settings honor each checkout’s config on fleet runs; CLI + caller env precedence unchanged; docs list which keys are per-checkout vs invocation-scoped.

---

## Round 24 — Wave 24 follow-ons

### 24.1 Enrich: network-only flag path untested

`--enrich-licenses-network` / `REPO_OPS_ENRICH_LICENSES_NETWORK` overrides LOCAL and is covered when both local and network are set. A run with **only** the network flag (no `--enrich-licenses` / `--enrich-licenses-local`) is not asserted end-to-end.

**Refs:** `license_policy.main`; `sbom.main`; `tests/test_license_policy.py`.

**Acceptance:** Regression where network-only enables enrichment with `allow_network=True`.

### 24.2 SHA refresh stays within-major under UPDATE_LATEST

`--precommit-refresh-sha` / `--submodule-refresh-sha` intentionally stay within the tagged major even when `REPO_OPS_PRECOMMIT_UPDATE_LATEST` / `REPO_OPS_SUBMODULE_UPDATE_LATEST` (or `--latest` / `--full`) is set. No regression proves SHA refresh does not cross major.

**Refs:** `precommit._resolve_sha_refresh`; `submodules` SHA refresh path; `tests/test_precommit_pins.py`; `tests/test_submodule_tag_advance.py`.

**Acceptance:** With refresh-sha + UPDATE_LATEST, a newer different-major tag does not become the refresh target; within-major newest still wins.
