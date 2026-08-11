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

## Round 33 — Wave 33 audit carry-forwards

### 33.1 Config exclude tests: chdir + real TOML instead of stubbing load_repo_config

Audit and pushed config-exclude tests monkeypatch `load_repo_config` to return exclude dicts. That proves `merge_exclude_patterns` wiring through `main`, not that `load_repo_config(Path.cwd())` is actually consulted. Prefer chdir + real `pyproject.toml` / `.repo-ops.toml` (or the existing e2e/dependabot chdir patterns).

**Refs:** `tests/test_audit.py`; `tests/test_pushed.py`; `tests/test_dependabot_sweep.py`; `tests/test_config_e2e.py`.

**Acceptance:** At least one audit and one pushed exclude test load exclude patterns from an on-disk checkout config without stubbing `load_repo_config`.

### 33.2 Audit gate env tests: isolate global config.toml

Autouse fixture stubs cwd `load_repo_config` to `{}`, but env-only gate tests do not pin `XDG_CONFIG_HOME` or stub `load_global_config`. Operator `~/.config/repo-ops/config.toml` can still inject `audit_fail_on*` via `apply_config_to_env` when those env vars are unset.

**Refs:** `tests/test_audit_config_gates.py`; `repo_ops_audit.audit.main`.

**Acceptance:** Env-only gate tests pin empty/global-free XDG config (or stub `load_global_config` → `{}`) so only the test env drives the gate.

### 33.3 Doctor: main/JSON path for mutated skill content drift

`test_probe_skill_drift_reports_mutated_install` covers `probe_skill_drift()` directly. Dangling-symlink coverage exists for `doctor.main` JSON; mutated SKILL.md content drift through `main` / `--json` is still unit-level only.

**Refs:** `tests/test_doctor.py`; `repo_ops_doctor.doctor.probe_skill_drift`.

**Acceptance:** A `doctor.main` (human and/or `--json`) test asserts mutated installed skill content appears in `skill_drift`.
