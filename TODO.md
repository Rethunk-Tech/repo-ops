# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue body still says “latest: v0.18.0”; bump the note when touching the issue.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

### 1.2 Package holds for pin ecosystems (container / Actions / pre-commit / submodules)

`[[hold]]` rules are documented as not yet consulted by container, GitHub Actions, submodule, and pre-commit checkers (same schema, no enforcement). Rust and Deno are also outside the current enforcement matrix.

**Refs:** `docs/usage.md` § Package holds (“Not yet wired…”); `repo_ops_core.holds`; checkers in `packages/repo-ops-container`, `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py`, `precommit.py`, `submodules.py`; `packages/repo-ops-rust`, `packages/repo-ops-js/src/repo_ops_js/deno.py`.

**Traps:** Actions/container “versions” are often digests or floating majors (`v4` vs SHA); a semver ceiling must define digest-refresh / unpin behaviour explicitly. Audit `--fix` advisory overrides must continue to win over holds (parity with JS/Python). Go already documents same-major-only ceilings — do not invent cross-major Go holds.

**Acceptance:** A hold on an Actions pin, container tag/digest, pre-commit `rev`, or submodule ref caps plan/apply the same way JS/Go/Python do; docs list each ecosystem; expired holds still skip without rewriting config.

### 1.3 Cargo workspace discovery: `exclude` / implicit members / `default-members`

Child Rust workspaces are suppressed only via explicit root `[workspace].members` globs. Cargo also auto-includes path deps, supports `exclude`, and uses `default-members` for default package selection — none of those are handled, so some layouts still double-count members as standalone rust workspaces.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/discovery.py` (`cargo_workspace_members`, `path_is_cargo_workspace_child`); Cargo Book [workspaces — members / exclude / default-members](https://doc.rust-lang.org/cargo/reference/workspaces.html); CHANGELOG known limitation under #52.

**Traps:** Empty/`members` omitted ≠ “no children”; path dependencies under the workspace root are members even without a glob. `default-members` affects *which* packages cargo operates on by default, not membership — collapsing discovery must not confuse the two. Prefer matching `cargo metadata --no-deps` over re-implementing glob rules if feasible.

**Acceptance:** Virtual and non-virtual workspaces with `exclude`, globs, and implicit path-dep members surface a single rust workspace at the root; no duplicate member rows in `plan --json` for those layouts.

### 1.4 Close stale issue #155 (pyrefly verify gate)

Verify already recognizes `pyrefly` in target-repo dev deps (`uv run pyrefly check .`). Issue [#155](https://github.com/Rethunk-Tech/repo-ops/issues/155) is still **OPEN** with no comments despite the 0.20.4 fix.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py` (mypy then pyrefly branches); `CHANGELOG.md` 0.20.4.

**Traps:** Closing is hygiene only — do not re-implement. Confirm the shipped branch matches the issue’s suggested detection surface (uv / hatch / poetry dev deps) before closing.

**Acceptance:** [#155](https://github.com/Rethunk-Tech/repo-ops/issues/155) closed with a pointer to the releasing commit/version; no code change required if coverage already matches.

---

## Round 2 — Ecosystem parity

### 2.1 Deno `--deno-targeted-bump` (JS targeted-bump parity)

Deno reports `behind_latest` and honors `--latest` / `REPO_OPS_DENO_UPDATE_LATEST`, but unlike yarn/npm/pnpm/bun there is no reversible per-package targeted-bump fallback to *move* remaining gaps.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/deno.py` (`check_deno` / `apply_deno`); JS siblings `targeted_bump_behind` in `repo-ops-js-{yarn,npm,pnpm,bun}`; CLI flags in `packages/repo-ops-core/src/repo_ops_core/cli.py`; `AGENTS.md` full-coverage sweep table.

**Traps:** Deno’s updater is `deno outdated --update` (± `--latest`); a targeted path must snapshot/restore `deno.lock` + import maps like the JS managers. Ghost-bump guard is best-effort when `node_modules` is absent — targeted bump must not assume npm layout. Do not fold this into `--latest` implicitly (same trap as other `--*-targeted-bump` flags). When this lands, fold it into `--full` (Wave 17 shipped the JS/digest umbrella without Deno).

**Acceptance:** `--deno-targeted-bump` (and config/env twin) moves still-behind direct deps after the bulk update, reverts on no-advance/install failure, appears in the full-coverage sweep docs beside the other four flags, and is enabled by `--full`.

### 2.2 Dependabot high-confidence supersession for poetry / PDM / pipenv locks

Scaffold treats `poetry.lock` / `pdm.lock` / `Pipfile.lock` as ecosystem indicators, but high-confidence supersession only parses npm/yarn/pnpm/bun/`uv.lock` (plus go.mod / Cargo.lock). Poetry/PDM/pipenv PRs fall through to low-confidence text search and are never auto-closed.

**Refs:** `packages/repo-ops-dependabot/src/repo_ops_dependabot/dependabot.py` (`_resolved_lockfile_versions` / `_lockfile_versions`); parsers in `packages/repo-ops-lockfiles` (`versions_pdm_lock`, `versions_pipenv_lock`; poetry via shared TOML `[[package]]` shape used in SBOM); license scanners already read these locks.

**Traps:** PEP 503 normalize Python names before lookup (uv path already does). Grouped Dependabot PRs must stay “all rows high-confidence” before `--close`. Reuse lockfile parsers — do not scrape lock text.

**Acceptance:** A Dependabot PR whose versions are already in `poetry.lock` / `pdm.lock` / `Pipfile.lock` is high-confidence superseded and closable with `--close`; JSON report marks `low_confidence=false`.

### 2.3 SBOM / license: enrich SPDX IDs instead of permanent `NOASSERTION`

`repo-ops sbom` always emits license `NOASSERTION` because lock parsers carry no license ids; `repo-ops license` is offline/best-effort and still leaves poetry/PDM/pipenv unknown without a venv, and containers always unknown.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/sbom.py`; `license_policy.py`; `docs/usage.md` § license scope table.

**Traps:** Network enrichment (crates.io / npm / PyPI) must be opt-in and cacheable — default SBOM path should stay offline. Do not invent licenses; prefer registry metadata or on-disk `LICENSE` / `METADATA`. Container images have no local license source today — keep them out of exit-`2` unknown unless a deliberate source is added.

**Acceptance:** Optional enrichment fills real SPDX ids for common npm/PyPI/crates packages in SBOM output; `license` unknown rate drops for poetry/PDM/pipenv when enrichment or a declared cache is enabled; offline default behaviour unchanged.

### 2.4 Rust upgrade-mode honesty vs aggressive bumps

`--latest` does not change Cargo behaviour (`cargo update` stays within existing constraints). Operators cannot opt into conservative vs aggressive Rust bumps the way Go (`patch`/`explicit`/`mvs`) or container cross-major gates work. Documented as intentional — still a fleet gap when Rust majors need a deliberate path (e.g. `cargo upgrade` / manifest edits).

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py`; `docs/usage.md` ecosystem upgrade-modes table; audit remediations already use `cargo update -p … --precise`.

**Traps:** Inventing a fake `--latest` that only re-runs `cargo update` would lie. Manifest-editing upgrades are a different product surface from lockfile refresh — keep the boundary explicit. Holds (1.2) should land before or with any aggressive Rust mode.

**Acceptance:** Either a real opt-in Rust upgrade mode (documented, gated, tested on manifest+lock) **or** a single canonical doc/AGENTS note that Rust majors are out-of-scope for repo-ops and must be done by hand — no half-mode.

---

## Round 3 — Operator DX / release hygiene

### 3.2 Automate GitHub Release post-cut cleanup

Every `v*` release still needs manual `gh release edit` (CHANGELOG section as notes) and `gh release delete-asset … default.gitignore`. Easy to forget; leaves a generic Release body and a junk asset.

**Refs:** `AGENTS.md` § Cutting a release step 6; `.github/workflows/release.yml` `build` job.

**Traps:** Prefer fixing the workflow artifact list (stop uploading `default.gitignore`) over forever deleting it. Notes extraction must match Keep a Changelog `## [X.Y.Z]` sections. Do not auto-publish to PyPI here (see 1.1).

**Acceptance:** A tagged release has correct CHANGELOG-derived notes and no `default.gitignore` asset without a human cleanup pass.

---

## Round 4 — Hygiene carry-forward

### 4.1 Supersede Dependabot PR #158 by hand

Open Dependabot PR [#158](https://github.com/Rethunk-Tech/repo-ops/pull/158) bumps the `dev-dependencies` group (`ruff`, `pyrefly`). Fleet rule: never merge Dependabot — apply the equivalent bump on `main` and close as superseded.

**Refs:** fleet `dependency-policy` skill; `uv.lock` / root `pyproject.toml`; `repo-ops apply --flat` on this checkout (or equivalent manual bump).

**Traps:** Local `main` may already be ahead of `origin/main`; reconcile lock drift before closing. Do not approve/merge the Dependabot PR.

**Acceptance:** `main` carries the intended ruff/pyrefly versions; #158 closed superseded; no Dependabot merge commit on the default branch.

### 4.2 Refresh issue #124 status text

Issue title/body still frame “latest: v0.18.0” while the tree has moved through 0.22.x. Operator hold remains valid; the status note is stale.

**Refs:** [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124); GitHub Releases / tags.

**Traps:** Do not treat a status edit as enabling PyPI. Keep the “intentional hold / not a CI bug” framing.

**Acceptance:** Issue title/body cite the current release line and restate the OIDC publisher gap without implying active chase.

### 4.3 Dependabot supersession for legacy `bun.lockb`

Inventory best-effort-parses binary `bun.lockb` via `bun` dump; dependabot high-confidence resolution only walks text `bun.lock`. Repos still on `lockb` keep low-confidence Dependabot closes.

**Refs:** `packages/repo-ops-inventory/src/repo_ops_inventory/counting.py` (`_versions_bun_lockb`); `packages/repo-ops-dependabot/src/repo_ops_dependabot/dependabot.py` (`bun.lock` only).

**Traps:** Invoking `bun` on lockb needs the binary on PATH and a timeout; cache or skip cleanly when bun is missing. Prefer encouraging text `bun.lock` migration over forever supporting lockb if fleet has moved on.

**Acceptance:** Either high-confidence supersession works for `bun.lockb` when `bun` is available, or docs explicitly say lockb is unsupported for `--close` and inventory remains the only consumer.

---

## Round 6 — Audit / security parity

### 6.1 Manager-aware Python `audit` (stop forcing `py-uv`)

`_detect_workspaces` labels any `pyproject.toml` / `uv.lock` tree as `py-uv` and runs `uv run pip-audit`. Poetry / PDM / pipenv / hatch / pip-tools layouts are misrouted or silently omitted (`requirements.in`+`requirements.txt` trees need a pyproject/uv.lock gate today).

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`_detect_workspaces`, `_audit_py_uv`); `docs/usage.md` § audit ecosystem table; plan/apply discovery in `packages/repo-ops-upgrade/src/repo_ops_upgrade/discovery.py`.

**Traps:** `uv run pip-audit` on a non-uv project can fail closed as “skipped” while the workspace looks audited in the roll-up. Prefer manager-native export + audit (or documented pip-audit-on-exported-reqs) over pretending everything is uv. Reuse upgrade discovery kinds — do not grow a second incompatible walker forever (same class as Cargo child suppression in 1.3).

**Acceptance:** Audit detects the real Python manager; poetry/PDM/pipenv/hatch/pip-tools repos are included with explicit ecosystem labels; wrong-manager skips are visible in JSON (`skip_reason`), not false-clean `ok` rows.

### 6.2 Deno vulnerability audit path

Deno has plan/apply/verify and remediations, but `audit_workspace` stops at go / node / py-uv / rust — fleet `audit` never sees Deno CVEs.

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`audit_workspace`); `packages/repo-ops-js/src/repo_ops_js/deno.py`; `packages/repo-ops-upgrade-remediations-deno`; `docs/usage.md` audit table.

**Traps:** Prefer `deno audit` (or the current Deno-documented equivalent) over inventing a third-party scanner. `--fix` must snapshot/restore `deno.lock` + import maps like apply. Missing `deno` on PATH should skip with reason, matching govulncheck/cargo-audit patterns.

**Acceptance:** `repo-ops audit` discovers Deno workspaces, normalizes severities into the shared count schema, and supports `--fix-plan` / `--fix` with reversible lock edits; usage table lists Deno beside the other ecosystems.

### 6.3 `audit --fix` should run verify gates

`apply_remediations` calls `orchestration.process_workspace(..., do_verify=False)`. CVE remediations can land broken trees with no post-fix gate; operators must remember a separate `apply` pass.

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`apply_remediations`); `packages/repo-ops-upgrade/src/repo_ops_upgrade/orchestration.py`; `docs/usage.md` § audit `--fix`.

**Traps:** Default-on verify changes bulk-audit timing — keep `--no-verify` escape and honor `[verify] skip`. Dirty-skip / `--allow-dirty` semantics must stay aligned with apply. Do not auto-commit (audit still has no commit path).

**Acceptance:** `audit --fix` runs the same per-manager verify as `apply` by default; failures appear in JSON with verify detail; `--no-verify` restores today’s behaviour.

### 6.4 SARIF must include unfixed findings

`build_sarif_report` emits one SARIF result per `RemediationItem` only. Critical/high findings without a known safe version (pip-audit “no fix”, govulncheck unknowns) vanish from code-scanning uploads while `--fail-on` still fails the CLI.

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`build_sarif_report`); `docs/usage.md` `--sarif-out`.

**Traps:** Keep repo-relative `artifactLocation.uri` + `%SRCROOT%` mapping. Distinguish fixable vs unfixed via SARIF `fixes` / properties rather than dropping rows. Severity → SARIF `level` must match the normalised worst severity already computed per workspace.

**Acceptance:** SARIF includes all findings at/above `--fail-on` (and unknown when `--fail-on-unknown`); fixable rows still carry remediation metadata; schema still validates for `upload-sarif`.

---

## Round 7 — Verify depth and pin-move parity

### 7.1 `uv` verify parity with hatch fast gates

After `uv sync --locked`, uv workspaces only optionally run `ruff`. Hatch runs `_verify_hatch_fast` (scripts, ruff, mypy/pyrefly, pytest). Migrating hatch→uv silently drops typecheck/test gates.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py` (`verify_workspace` uv vs hatch branches, `_verify_hatch_fast`); `docs/usage.md` verify notes.

**Traps:** Share gate helpers rather than copy-paste hatch logic. Respect `[verify] skip` / timeout. Pair with 5.2 so pyrefly scope matches the target’s config when that gate runs.

**Acceptance:** uv workspaces with mypy/pyrefly/pytest (or equivalent scripts) run the same fast gates hatch does after a locked sync; documented in usage.

### 7.2 Poetry / PDM / pipenv verify beyond lock integrity

Poetry verify is `poetry check`; PDM is `pdm sync --clean`; pipenv is `pipenv verify`. Lock integrity can pass while lint/typecheck/tests never run — weaker than node/go/uv-hatch paths.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py` (poetry/pdm/pipenv branches); manager packages under `packages/repo-ops-py-{poetry,pdm,pipenv}`.

**Traps:** Do not invent manager-specific mini-CI — prefer configured project scripts / known tools already on the hatch/uv path. Missing binaries should fail closed with a clear reason, not skip silently. Keep timeouts consistent with other verify gates.

**Acceptance:** Each manager runs a documented gate stack (lock sync/check + lint/typecheck/test when configured); failures block apply like other ecosystems; usage lists the stack per manager.

### 7.3 Opt-in SHA refresh for pre-commit and submodule pins

Actions/container already have `--actions-refresh-sha` / `--container-refresh-sha`. Pre-commit SHA `rev:` and submodule SHA-only HEADs stay permanent report-only with no move path — same “why didn’t apply move this?” wall operators hit before those flags existed.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py` (SHA rev → report-only); `packages/repo-ops-upgrade/src/repo_ops_upgrade/submodules.py` (SHA-only HEAD → report-only); contrast `actions.py` / `repo-ops-container`; `AGENTS.md` full-coverage sweep table.

**Traps:** Refresh must stay digest-within-intent (same hook repo / same submodule URL), not a silent major leap. Compose into the eventual `--full` umbrella (3.1) once named. Holds (1.2) should define ceiling behaviour before aggressive refresh. Container SHA-without-tag remains a separate harder case — do not pretend a tag-less digest has a refresh target.

**Acceptance:** Opt-in flags (e.g. `--precommit-refresh-sha`, `--submodule-refresh-sha`) advance those pins when resolvable; docs/AGENTS list them beside the existing refresh flags; without the flags, behaviour stays report-only.

### 7.5 `--commit-umbrella` help vs behaviour

CLI help says umbrella commits are a no-op when `--submodule-order` is unset. Code enables via `REPO_OPS_COMMIT_UMBRELLA=1` whenever `submodule_links` were discovered (children-first *or* `--submodules` scan) — operators can skip the wrong flag combo.

**Refs:** `packages/repo-ops-core/src/repo_ops_core/cli.py` (`--commit-umbrella` help); `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`need_submodule_scan`, umbrella commit block); `post_apply_commit.commit_umbrella_submodule_pointers`; distinct from stale “deferred to cli.py” comment (3.4).

**Traps:** Fix help **or** tighten gating — pick one. Correct child→parent ordering still needs `--submodule-order=children-first` for apply itself; do not conflate order with commit-umbrella. Related: `--pr` / `--resume` currently iterate `child_outcomes` only (file as follow-on if not folded here).

**Acceptance:** Help matches actual gating; `docs/bulk-maintenance.md` states the required flag combo for child→parent apply + umbrella pointer commits.

---

## Round 8 — Fleet DX / cross-subcommand gaps

### 8.1 Apply `--resume` docs vs commit-gated checkpoints

CLI help correctly says `--resume` requires `--commit`. `docs/usage.md` § Apply resume describes checkpointing “after each successful repo” with no commit requirement — operators enable `REPO_OPS_RESUME=1` alone and get zero checkpoints.

**Refs:** `docs/usage.md` § Apply resume; `packages/repo-ops-core/src/repo_ops_core/cli.py` (`--resume` help); `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (checkpoint only when `CommitOutcome.status == "committed"`).

**Traps:** Either checkpoint post-verify success (separate from post-commit SHA) **or** make docs/help identical about the commit gate. Stale checkpoints after manual reset are a related hygiene gap (clear surface) — do not leave “edit `~/.cache/repo-ops/` by hand” as the only recovery.

**Acceptance:** Docs and `--help` agree on when checkpoints are written; a documented way exists to clear a stale checkpoint; resume skip rows state which gate matched.

### 8.2 `--exclude` / config `exclude` on audit, dependabot, pushed

`exclude` / `--exclude` filter plan/apply only. Global `~/.config/repo-ops/config.toml` `exclude = ["archive/*"]` silently does nothing for audit / dependabot / pushed — archived repos still get scanned.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (exclude filtering); `packages/repo-ops-core/src/repo_ops_core/config.py` (`exclude`); `packages/repo-ops-audit`, `packages/repo-ops-dependabot`, `packages/repo-ops-pushed` (no exclude merge).

**Traps:** Keep config-file-only semantics for the `exclude` key (no inventing an env array). Shared helper beats three one-off filters. JSON summary should count excluded repos for parity with plan/apply.

**Acceptance:** `--exclude` and config `exclude` are honored on audit, dependabot, and pushed; excluded repos appear in summary counts; docs state the shared behaviour.

### 8.3 `pushed` remote/API fallback (local reflog blind spot)

`repo-ops pushed` only counts this-machine `refs/remotes/*` reflog “update by push” entries. Fresh clones, CI runners, and pushes from another workstation report zero even when GitHub has activity.

**Refs:** `packages/repo-ops-pushed/src/repo_ops_pushed/audit.py` (module docstring, `PUSH_RE`); `docs/usage.md` pushed blurb.

**Traps:** Document the limitation even if code stays local-only. Any `--source=gh` path needs authenticated `gh`, rate-limit discipline, and must not invent pushes the API cannot attribute. Reflog expiry under gc is the same class of false zero.

**Acceptance:** Usage calls out the machine-local limitation prominently; optional remote source (or an explicit “unsupported on fresh clones” exit/warning) so operators do not trust empty tables as “no pushes”.

### 8.4 Actions org-policy block/warn mode

`--actions-org-policy` records `status="policy"` rows but “never blocks pin bumps”. Unlike `--actions-lint-level block|warn|off`, policy violations cannot stop apply from rewriting disallowed actions.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py` (~policy check / “report-only; never blocks”); `packages/repo-ops-core/src/repo_ops_core/cli.py` (`--actions-org-policy`, `--actions-lint-level`).

**Traps:** Mirror the lint-level pattern rather than a one-off boolean. Default must stay report-only for back-compat. Policy fetch failures stay non-fatal (already best-effort).

**Acceptance:** A documented level (`block`/`warn`/`report`) can skip or warn on violating pin rewrites; JSON distinguishes policy-report vs policy-blocked; default remains today’s report-only behaviour.

### 8.5 Dependabot `--scaffold` ecosystem gaps

Scaffold emits coarse `pip` for any Python indicator, `npm` for any Node lock (including bun), and omits `deno` / `pre-commit` entirely — generated configs under-serve modern fleet layouts.

**Refs:** `packages/repo-ops-dependabot/src/repo_ops_dependabot/dependabot.py` (`detect_ecosystems`, `_NODE_INDICATORS`, `_PY_INDICATORS`); Dependabot docs for valid `package-ecosystem` values; distinct from supersession precision (2.2 / 4.3).

**Traps:** Only emit ecosystems Dependabot actually supports — do not invent fake ecosystem strings. Bun may still map to `npm` upstream; document that. Keep prune-by-regeneration behaviour (`--write` replaces the file).

**Acceptance:** Scaffold detects deno and pre-commit where indicators exist; Python/manager notes in docs list scaffold coverage vs supersession coverage separately; dry-run output matches what `--write` would emit.

### 8.6 Document remediation catalog + `--only-outdated`

Post-apply remediation ids (`biome_schema`, `playwright_browsers`, `codegen`, `package_manager_pin`, `gofmt`, `go_generate`, `ruff_fix`, `cargo_fix`, `deno_fix`, …) are only partially listed in `--skip-remediations` help. `--only-outdated` is shipped for plan/apply human output but absent from usage/AGENTS/skill — agents rediscover fleet noise the hard way.

**Refs:** `packages/repo-ops-upgrade-remediations-{js,go,py,rust,deno}`; `packages/repo-ops-core/src/repo_ops_core/cli.py` (`--skip-remediations`, `--only-outdated`); `docs/usage.md`; `AGENTS.md`; bundled skill under `packages/repo-ops/src/repo_ops/skills/repo-ops/`.

**Traps:** Docs describe current ids only — no changelog archaeology. Note default-off hooks (`REPO_OPS_REMEDIATE_GO_GENERATE`). `--only-outdated` filters human output; confirm JSON behaviour before documenting (do not claim JSON is filtered if it is not).

**Acceptance:** Usage has a remediation table (id, ecosystem, trigger, default on/off); `--skip-remediations` help lists all ids; `--only-outdated` appears in usage + AGENTS with accurate human-vs-JSON behaviour.

### 8.7 `install-skill` stale-copy detection

`install-skill` skip-on-exists unless `--force`. After `uv tool` upgrades, Cursor/Claude skill copies keep stale `$FULL_SWEEP_FLAGS` / subcommand lists until someone remembers `--force`.

**Refs:** `packages/repo-ops/src/repo_ops/install_skill.py`; bundled skill `packages/repo-ops/src/repo_ops/skills/repo-ops/`; optional doctor hook in `packages/repo-ops-doctor`.

**Traps:** Compare content hash or bundled package version, not mtime alone (editors bump mtime). `--check` / doctor must stay read-only. Do not auto-overwrite without `--force`.

**Acceptance:** `install-skill --check` (and/or doctor) warns when the installed skill drifts from the bundled copy; `--force` refresh remains the write path; docs mention the upgrade gotcha.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: per-checkout config besides commit-agent model

`run_upgrade` still loads `load_repo_config(Path.cwd())` once and injects via `apply_config_to_env`. Wave 17 made `commit_agent_model` resolve per `bundle.git_repo` at draft time; other config keys (provider, exclude, jobs, …) still ignore each target checkout’s `config.toml` on multi-root apply.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`run_upgrade` config load); `repo_ops_core.config.apply_config_to_env` / `resolve_setting`; `commit_agent_cursor._resolve_agent_model` (per-repo precedent).

**Traps:** Do not double-apply env injection mid-flight in a way that leaks one repo’s settings into the next. Prefer resolve-at-use (like commit-agent model) over mutating process-wide env per checkout.

**Acceptance:** Documented settings that are repo-scoped honor each checkout’s config on fleet `plan`/`apply`; global + env + CLI precedence unchanged.

### 9.2 `commit_agent_provider` settings-layer parity

`commit_agent_provider_name()` still reads only `REPO_OPS_COMMIT_AGENT_PROVIDER` from the environment (relying on `apply_config_to_env`), unlike `commit_agent_model` which now calls `resolve_setting` with per-repo config.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/commit_agent_providers.py`; Wave 17 `commit_agent_cursor._resolve_agent_model`.

**Traps:** Keep the same fail-closed unknown-provider behaviour. Avoid loading config on every provider lookup if hot — cache or resolve once per bundle.

**Acceptance:** Provider selection honors env, global config, and per-checkout config with the same precedence story as `commit_agent_model`.
