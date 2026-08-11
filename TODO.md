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

`tests/test_config_e2e.py` fleet harness covers pnpm, npm, container, pre-commit, and submodule per-checkout readers. Unit coverage for yarn/bun/pnpm update-latest/targeted-bump and npm targeted-bump lives in `tests/test_js_manager_checkout_flags.py` (mocked `load_repo_config`). Deno still has direct helper coverage only. Fleet-path e2e for yarn/bun/deno remains deferred.

**Refs:** `tests/test_config_e2e.py`; `tests/test_js_manager_checkout_flags.py`; `repo_ops_js_yarn.yarn`; `repo_ops_js_bun.bun`; `repo_ops_js.deno`.

**Acceptance:** Fleet e2e asserts divergent per-checkout resolution for yarn and bun update-latest/targeted-bump (and deno via fleet path, not only direct helpers).

---

## Round 34 — Wave 34 audit carry-forwards

### 34.1 Doctor: retire stubbed skill-drift main test or keep as unit-style

`test_main_reports_mutated_skill_content_as_drift` covers real mutated install through `doctor.main`. Sibling `test_main_warns_when_installed_skill_drifts` still stubs `probe_skill_drift` entirely. Either remove the stubbed test as redundant or keep it explicitly as a cheap unit-style path.

**Refs:** `tests/test_doctor.py`.

**Acceptance:** Stubbed main skill-drift test is removed, or a short comment/docstring marks it as unit-style probe stub (no history prose).

### 34.2 Config-exclude tests: JSON summary.excluded parity

On-disk config exclude tests in audit/pushed assert human `excluded:` lines / seen repos. CLI exclude tests also assert `--json` `summary.excluded`. Optional parity for the config-driven paths.

**Refs:** `tests/test_audit.py`; `tests/test_pushed.py`.

**Acceptance:** At least one audit and one pushed on-disk-config exclude test assert JSON `summary.excluded` (or equivalent) without stubbing `load_repo_config`.

### 34.3 Audit gate TOML tests: pop all apply_config_to_env keys

TOML config gate tests `finally`-pop the single promoted key under test. Belt-and-suspenders: pop every key `apply_config_to_env` may set from the written `config.toml` so leftover env cannot leak across tests.

**Refs:** `tests/test_audit_config_gates.py`; `repo_ops_core.config.apply_config_to_env`.

**Acceptance:** TOML-driven gate tests clear all env keys their fixture config can promote, not only the asserted gate key.
