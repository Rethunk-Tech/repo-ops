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

**Traps:** Actions/container “versions” are often digests or floating majors (`v4` vs SHA); a semver ceiling must define digest-refresh / unpin behaviour explicitly. Audit `--fix` advisory overrides must continue to win over holds (parity with JS/Python). Go already documents same-major-only ceilings — do not invent cross-major Go holds.

**Acceptance:** A hold on an Actions pin, container tag/digest, pre-commit `rev`, or submodule ref caps plan/apply the same way JS/Go/Python do; docs list each ecosystem; expired holds still skip without rewriting config.

---

## Round 7 — Verify depth and pin-move parity

### 7.3 Opt-in SHA refresh for pre-commit and submodule pins

Actions/container already have `--actions-refresh-sha` / `--container-refresh-sha`. Pre-commit SHA `rev:` and submodule SHA-only HEADs stay permanent report-only with no move path — same “why didn’t apply move this?” wall operators hit before those flags existed.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py` (SHA rev → report-only); `packages/repo-ops-upgrade/src/repo_ops_upgrade/submodules.py` (SHA-only HEAD → report-only); contrast `actions.py` / `repo-ops-container`; `AGENTS.md` full-coverage sweep table.

**Traps:** Refresh must stay digest-within-intent (same hook repo / same submodule URL), not a silent major leap. Compose into the `--full` umbrella once named. Holds (1.2) should define ceiling behaviour before aggressive refresh. Container SHA-without-tag remains a separate harder case — do not pretend a tag-less digest has a refresh target.

**Acceptance:** Opt-in flags (e.g. `--precommit-refresh-sha`, `--submodule-refresh-sha`) advance those pins when resolvable; docs/AGENTS list them beside the existing refresh flags; without the flags, behaviour stays report-only.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: per-checkout config besides commit-agent model/provider

`run_upgrade` still loads `load_repo_config(Path.cwd())` once and injects via `apply_config_to_env`. Wave 17/18 made `commit_agent_model` and `commit_agent_provider` resolve per `bundle.git_repo` at draft time; other config keys (exclude, jobs, …) still ignore each target checkout’s `config.toml` on multi-root apply.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`run_upgrade` config load); `repo_ops_core.config.apply_config_to_env` / `resolve_setting`; `commit_agent_cursor._resolve_agent_model` / `commit_agent_providers.commit_agent_provider_name` (per-repo precedent).

**Traps:** Do not double-apply env injection mid-flight in a way that leaks one repo’s settings into the next. Prefer resolve-at-use (like commit-agent model/provider) over mutating process-wide env per checkout.

**Acceptance:** Documented settings that are repo-scoped honor each checkout’s config on fleet `plan`/`apply`; global + env + CLI precedence unchanged.

---

## Round 21 — Wave 21 follow-ons

### 21.1 License enrichment: local-only mode + safer text classification

`--enrich-licenses` always sets `allow_network=True`. Air-gapped operators need local METADATA/LICENSE enrichment without registry calls. BSD-family LICENSE text classification can mis-label BSD-3 (and relatives) as `BSD-2-Clause` via a shared needle — prefer `NOASSERTION` on ambiguity.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/license_enrich.py`; `sbom.py` / `license_policy.py` enrich wiring.

**Acceptance:** Opt-in local-only enrichment (flag or env); ambiguous LICENSE text stays unknown rather than a wrong SPDX id.

---

## Round 22 — Wave 22 follow-ons

### 22.1 Deno targeted-bump: resolve landed version into Change.to

`targeted_bump_behind` currently keeps the planned `Change` on success (snapshot delta only). Prefer recording the post-update resolved version when parseable from lock/import map, matching bun/npm applied-Change semantics.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/deno.py` (`targeted_bump_behind`).

### 22.2 Audit Python: hatch vs uv disambiguation when both signals present

A tree with both `uv.lock` and hatchling metadata may need an explicit precedence rule documented in usage (which label wins).

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`_detect_workspaces`).
