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

## Round 35 — Wave 35 in flight

### 35.1 Dependabot on-disk exclude + JSON `excluded_repos`

Mirror audit/pushed hermetic on-disk exclude tests: write `[tool.repo-ops] exclude` in `pyproject.toml`, do not mock `load_repo_config`, assert scan set + human `excluded: N repo(s)` + JSON `summary.excluded_repos`.

**Owns:** `tests/test_dependabot_sweep.py`

### 35.2 SETTINGS_MAP ↔ SETTINGS_TABLE parity guard

One invariant: map keys == SETTINGS_TABLE names; env/config columns align per row.

**Owns:** `tests/test_core_config.py`

### 35.3 `resolve_setting` env coverage holes

Env-backed unit tests for `actions_policy_level`, refresh-sha settings, `commit_agent_provider`, yarn/bun/pnpm/deno update-latest and bun/deno targeted-bump. No fleet e2e.

**Owns:** `tests/test_config.py`

### 35.4 `pm_exec_prefix` npm/pnpm parity

Assert npm → `npx --yes`, pnpm → `pnpm exec` beside existing yarn/bun tests.

**Owns:** `tests/test_upgrade.py`

### 35.5 Doctor `--json-out` includes `skill_drift`

Mutated skill install; `--json --json-out PATH` file payload lists drift; stdout path-only.

**Owns:** `tests/test_doctor.py`
