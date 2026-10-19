# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.25.5] - 2026-10-18

### Fixed

- `apply` skips package lifecycle scripts and refuses Corepack URL locators.
- Container digest checks follow a token realm only over https on the registry's own host (Docker Hub's auth host excepted).
- `apply` refuses more secret file names and fails closed when it owns no edits.
- Run logs are redacted, and persisted verify errors are truncated.

## [0.25.4] - 2026-10-18

- feat(apply): digest-check rewritten container image refs
- fix(apply): classify pre-commit-only apply as chore(hooks)
- fix(apply): scope and classify leftover bun.lockb
- feat(apply): add --manager to select a registered plugin
- fix(apply): say why --no-verify skips the post-apply commit
- fix(apply): enumerate container files in mixed commit subjects

## [0.25.3] - 2026-10-18

### Added

- The `gates` `report.json` summary includes `record_partial`, the count of
  rows whose `record_partial` is true.
- A `gates` row that still runs under `--skip-fresh` includes
  `gate_record_reason` (`missing`, `stale`, `partial`, or `failed`), the same
  reason `gate --json record` reports. A skipped fresh row keeps
  `skip_reason: "fresh"` and omits `gate_record_reason`.
- A `gates` report row includes `skip_reason: "fresh"` when `--skip-fresh`
  skipped a fresh non-partial pass, and `record_partial: true` when the run
  happened because that matching record was partial. Both keys are omitted
  otherwise.
- `gates --skip-fresh` skips a repo whose schema-2 result matches this HEAD
  and working tree, exited 0, and is not partial. A partial record still runs,
  as does a dirty tree, or a failed, stale, or missing record. The summary and
  report count skipped repos as skipped.

### Changed

- Verify requires `gate`. Without it, or where it detects no gate, verify fails
  instead of running the first of `ci`, `ci:pushgate`, `build`, `lint`,
  `go build`/`go test`, `cargo check`/`cargo test`, or the Python
  pytest/mypy/pyrefly/ruff ladder. Lockfile steps are unchanged.

### Fixed

- `apply` commits only owned edits, vetoes on action fail, restores a 600s
  timeout, keeps Actions and submodule work on their filters, includes the
  SOUP table in the deps commit, and stages Deno, pip-tools, precommit, and
  Python files ruff rewrites. Post-apply collection covers Deno manifests and
  Cargo.toml. Untrusted paths are contained; gh tokens are discarded.
- `apply` snapshots pip-tools `requirements.in` for revert, treats unset
  `no_verify` as false on mock args, verifies gitignored lockfile writes, and
  reinstalls poetry, pipenv, rust, go, deno, and pip-tools on revert. Nested
  `package.json` objects stay inside dep sections.
- Submodule bumps fail when fetch or checkout fails. SOUP versions resolve
  from the workspace lockfile. JS Corepack runs without activating a shared
  shim. Verify runs gate for Deno and fails without a compiler, and runs
  pytest instead of collecting tests.
- `gates --skip-fresh` treats a fresh partial pass as not fresh enough to
  skip. `partial: true` with exit 0 still runs `gate`. A fresh non-partial
  pass still skips.
- `gates` names a timed-out or interrupted gate, and its log, from gate's
  result record. The text summary is used only when that record is not this run.
- `gates` reads result schema 2, and only when `status` is this working tree.
  A record that is not this run falls back to the text, including `TIMEOUT` lines.
- `gates` leaves out e2e gates the way `verify` does. `gates --e2e` runs them.
  The summary counts the repo for the gates that ran, and prints how many e2e
  gates were left out.
- Local `gate` caps pytest-xdist at 8 workers.

## [0.25.2] - 2026-10-09

### Added

- `upgrade` can sync versions in an optional `SOUP.md` on a dependency bump,
  writing the lockfile-resolved version.

### Fixed

- Actions scanning only ever read `.github/workflows/*.yml`, so a `uses:` pin
  inside a composite action's `action.yml`/`action.yaml` never got bumped or
  policy-checked. Both files are now discovered and scanned the same way,
  respecting the repo's exclude globs.
- `dependabot` never read workflow `uses:` pins, so every GitHub Actions bump
  PR came back unknown and `--close` could not close it. Pins in workflows and
  composite `action.yml` files now resolve, a SHA pin by its `# vX.Y` comment.
- Per-repo `exclude` skips workspaces by repo-relative path; discovery skips
  directories the repository gitignores and `go.mod` files without a `module`
  directive.
- `apply` rolls back only repo-ops' own writes when verify fails, omits
  discovery skips and warnings for excluded repos, and an umbrella parent no
  longer skips as dirty because a child was updated.
- Commits resync every path a pre-commit hook adds or rewrites, not just the
  planned files.
- Go upgrades run module commands with `GOWORK=off` and avoid versions the Go
  vulnerability database lists; Bun upgrades refresh the lockfile and move the
  `packageManager` pin to the registry version.
- pip-tools compiles keep the requirements header free of the hold-file path,
  use a stable hold-constraints filename, delete the file uv creates for a
  stdout compile, and lift an exclusive bound so it still contains latest.
- Single-update group Dependabot PRs are read from their `Updated X from` text.
- The local gate enforces the CI coverage floor, the verify step names the gate
  step that failed, and the wheel ships `py.typed`.

## [0.25.1] - 2026-09-21

### Fixed

- Digest-pinned container images never left their original tag. `check_container`
  refreshed only the pinned tag's digest and returned before the tag resolver
  ran, so `image:tag@sha256:...` stayed put while the report showed no drift.
  Tag and digest now advance together.
- Container Dependabot PRs could never be resolved as superseded: the version
  map read go.mod, Cargo.lock and pyproject.toml but not Dockerfile `FROM`
  pins, so every image bump reported as unknown.
- Dependabot PR titles carrying a doubled conventional-commit scope
  (`chore(deps)(deps): bump ...`, emitted when a repo sets its own
  commit-message prefix) were reported as unparseable.

## [0.25.0] - 2026-09-18

repo-ops is now an apply-first tool. Across 362 recorded invocations the operator ran `apply` and `plan` and almost nothing else, passed the same handful of flags every time, and read a 1,126-word `apply --help` 114 times. This release keeps the three commands that carry the work, makes the flags everyone passed the defaults, and deletes the rest: `repo-ops apply` with no arguments now updates, verifies and commits every checkout under the default roots.

### Upgrading from 0.24

| 0.24 | 0.25 |
| --- | --- |
| `repo-ops apply --commit=conventional` | `repo-ops apply` (commits by default; `--no-commit` to opt out) |
| `apply --flat --allow-dirty --on-verify-fail=keep /path` | `repo-ops apply /path` (a named root implies all three) |
| `--log-file`, `--json`, `--json-out`, `--compact` | every run writes `$XDG_STATE_HOME/repo-ops/runs/<utc>-<cmd>/{log,report.json}` and prints both paths last |
| `--conservative`, `--go-upgrade-mode`, `--actions-*`, `--migrate-to-uv-lock` | removed; the full sweep is the only mode |
| `repo-ops audit` | `osv-scanner`, `govulncheck`, `uv audit` |
| `repo-ops license`, `repo-ops sbom` | `osv-scanner --licenses` |
| `repo-ops inventory` (`--status`), `repo-ops pushed` | `project-register` |
| `repo-ops doctor` | the ecosystem CLIs' own `--version` |
| `repo-ops install-skill` | `bunx skills add Rethunk-Tech/repo-ops` |
| `dependabot --scaffold/--write/--schedule` | removed (they generated the PRs the fleet closes) |
| `REPO_OPS_*` environment variables | removed; the only config is two files and three keys |
| `[tool.repo-ops]` in `pyproject.toml` | move it to `.repo-ops.toml` |

### Changed

- **BREAKING: `apply` commits each verified repo by default.** One conventional commit per repo, staging only the files apply touched (`git commit -- <paths>`), never `.env` or key material, and never pushing. `--no-commit` leaves the tree dirty; `--no-verify` implies it. The `agent` commit mode and its Cursor SDK extra are gone: SDK pipelines do not live in product repos.
- **BREAKING: dirty and failure handling follow from whether roots were named.** With no roots, `apply` sweeps `DEFAULT_ROOTS`: dirty repos are skipped, a repo whose verify fails is reverted, and it is retried once on its own after the sweep (9 of 9 failures in one sweep were concurrency artefacts that passed alone). A named root is a checkout the operator is working in: it is processed even when dirty, only apply's own files are committed, a failed verify keeps the changes for inspection, and `--exclude` never drops it. This replaces `--flat`, `--allow-dirty` and `--on-verify-fail`, which were passed on 88/88, 29/30 and 15/15 explicit-root runs.
- **BREAKING: one mode, the full sweep.** Every ecosystem crosses ranges and majors, runs a targeted bump for anything still behind, and refreshes or pins SHAs for Actions, containers, pre-commit hooks and submodules. `--conservative`, the 22 sweep settings behind it (0 set anywhere on the fleet), the Go `patch`/`mvs` modes, the Actions tag-style/unpin/policy-level knobs and the hatch migration switch are deleted. Floating Actions tags become SHA pins, hatch projects always gain a `uv.lock`, and the org allowed-actions check runs whenever `gh` is authenticated. Held packages are the way to stay behind on purpose.
- **BREAKING: config is two files and three keys.** `~/.config/repo-ops/config.toml` and a per-repo `.repo-ops.toml`, keys `exclude`, `[[hold]]` and `[verify]`. `exclude` is the union of both files and `--exclude`. All 48 `REPO_OPS_*` environment variables, the `pyproject.toml [tool.repo-ops]` layer and the timeout and concurrency settings are gone; timeouts are constants. A `.repo-ops.toml` in a workspace below the repo root can carry its own `[verify]`, so `skip = true` opts out one workspace.
- **BREAKING: six flags.** `plan` and `apply` take `root ...`, `--exclude`, `--jobs`, `-v` and `--filter`; `apply` adds `--no-commit` and `--no-verify`; `dependabot` takes the same scan flags plus `--close`. All three share one parser, so a flag means the same thing everywhere and the 13 "X requires Y" checks (9 of them silent no-ops) went with their flags. `apply --help` is 139 words, down from 1,126.
- **Every run writes a run directory.** `log` and `report.json` land under `$XDG_STATE_HOME/repo-ops/runs/<utc>-<cmd>/` (newest 50 kept), and their paths are the last line printed, replacing `--log-file` and the `--json`/`--json-out` pair that errored 16 times when only one half was passed. The human report lists only repos with something to show.
- **Verify runs the gates a project declares.** Where the `gate` CLI detects gates, verify runs them (Makefile targets, turbo tasks, `.gate.toml`), tests included, after the ecosystem's lockfile check. Measured on Routed, the old Node ladder ran `bun run build` where `gate` detects five gates; on paper-trail the Python ladder ran only a pytest collection where `gate` detects six. The built-in ladders remain for repos without `gate`.
- **Umbrella repos get their pointer commit automatically.** Children are applied first, then each parent gets one `chore(submodules)` commit staging exactly the gitlinks of children committed this run. A Bastion sweep previously ended with 17 dirty gitlinks to commit by hand.
- **Actions tag lines always carry a SHA**, as `gh api` returns them, so the name-only parsing path and the tag-normalise branch it fed are gone.

### Added

- **Expo apps hold their SDK-coupled packages at the installed SDK's bundled versions.** A default sweep over majordomo moved `react-native`, `react-native-gesture-handler` (`~2.32` to `3.3`) and nine more `apps/mobile` packages past what Expo SDK 57 bundles, and expo-doctor broke. For a workspace depending on `expo`, every package in the installed `expo/bundledNativeModules.json` is held at its bundled spec, `expo` at its installed major, and `expo.install.exclude` opts out. Rows carry the reason (`expo SDK 57 bundles ~2.32.0`).
- **`manifest_format` remediation.** bun, npm and pnpm re-serialise `package.json` fully expanded, so a repo whose Biome config sets `json.formatter.expand: "never"` failed verify on every manifest a sweep touched (prepaudit: five). The repo's own `biome format --write` now runs on exactly the rewritten manifests when a Biome config governs them.

### Fixed

- **Auto-commits could sweep up whatever was already staged.** The commit now names its paths, so an operator's staged work stays out of repo-ops' commit.
- **Child commits in umbrella repos failed on `index.lock: File exists`.** A parallel `git status` in the umbrella took the submodules' index locks (27 of 300 adds failed in a local repro). `GIT_OPTIONAL_LOCKS=0` is set for repo-ops and every subprocess (0 of 300).
- **A failed verify now returns the repo to its exact pre-apply state.** Revert restored only lockfiles and manifests, so a `biome migrate` `$schema` bump and the upgraded `node_modules` survived (citadel and six Biome repos). Every file changed since apply is restored, edits that predate apply are kept, the tree is reinstalled from the restored lockfile, and a verify that raises reverts too.
- **Workspace members were never checked.** bun and pnpm outdated scope to the cwd, and npm saved member bumps into the root manifest. Members are now checked and bumped from their own directories.
- **Private members' peer pins follow their own dependency bumps.** A sweep moved `next` to 16.3.5 but left `peerDependencies.next` at 16.3.1 (prepaudit and caldera), and bun warned on every install. Exact peer pins move and `^`/`~` floors rise, never fall, subject to holds at the member path.
- **Holds scoped to a member were reported as `(latest)`.** Entries now carry their member path, holds resolve there, and `packages_held` counts them.
- **Go holds on untagged modules blocked nothing.** Pseudo-versions compared as `0.0.0`; they now order by base version then commit time and clamp exactly.
- **Actions pins:** sub-path actions (`owner/repo/setup-go`) fetch tags from `owner/repo`; two-part `vN.M` tags order by semver; the `# vX.Y.Z` comment is rewritten to the tag the new SHA resolves to.
- **bun.lock's workspace mirror held the literal `latest`** after `bun update --latest` (91 entries across 26 repos). It is resynced after an apply that changed something.
- **bun checks no longer probe `bun outdated --json`**, which bun 1.4 does not support; every bun workspace spent a failing subprocess on it.
- **pip-tools verify treated `zope.interface` and `zope-interface` as different packages** and reported drift. Names are normalised per PEP 503.
- **`dependabot` matched skip directories anywhere in a repo's absolute path** rather than below the repo root, and used either the repo or the global exclude list instead of both.

### Removed

- **Commands:** `audit`, `license`, `sbom`, `doctor`, `inventory`, `install-skill` and `pushed`, with their packages. Each had a better-maintained replacement (see the table above) or no recorded use; `audit` alone was 2,756 lines, all of its recorded runs against a repo-ops test probe.
- **`--pr`, `--pr-base`, `--branch-prefix`** (pushed without per-push approval), **`--resume`** and its checkpoint, and the remaining zero-use flags (`--top`, `--only-outdated`, `--strict-exit`, `--fail-fast`, `--skip-remediations`, `--verify-test`, `--max-failures`, `--quiet`, `--log-level`, `--follow-symlinks`, `--manager`, `--submodules`, `--submodule-order`).
- **Binary `bun.lockb` support.** No fleet repo has one and bun 1.4 no longer writes it.
- **The `go_generate` remediation and the Go directive/toolchain toggles**; `plan` no longer mutates `go.work`.
- **Docs:** `HUMANS.md`, `docs/installation.md` and `docs/programmatic-imports.md` (no importer exists); the skill is one `SKILL.md` under `skills/repo-ops/`. Docs and skill went from about 9,600 words to 2,474.

### Internal

- Duplicated helpers consolidated: one GitHub-remote parser, one workspace-member matcher, one TOML lock parser, one requirements parser, one hold check (`repo_ops_core.holds.target_within_hold`), one lock clamp for uv/pdm/poetry/pipenv.
- Tests: 30 duplicate or dead-feature tests and 40 unused helpers removed after per-test coverage comparison; shared `fake_run` and `upgrade_args` fixtures, the latter built from the real parser so it cannot drift.

## [0.24.0] - 2026-08-18

### Security

- **A container registry could point the token fetch at any URL scheme.** The bearer-challenge flow takes its token realm from the `WWW-Authenticate` header the registry itself returns, and that URL went straight into `urllib.request.urlopen` — a `file:` or `ftp:` realm would have been opened as such. Every registry and package-metadata fetch now goes through one opener that refuses anything but http/https before the request is built.

### Added

- **`inventory --status`: where every checkout stands, in one table.** Reports `repo | branch | dirty | ahead | behind` for each discovered repository, skipping manifest scanning entirely (119 repos in ~0.4s on the reference fleet). `dirty` counts working-tree entries, counting a rename once rather than twice. `ahead`/`behind` compare `HEAD` against its upstream; a branch with no configured upstream falls back to `origin/HEAD`, but a detached `HEAD` deliberately does not — a submodule parked at its parent's pin has not drifted, and scoring it against `origin/HEAD` would flag every pin upstream had moved past. Repos with no comparison ref print `-`, which is distinct from a measured `0`. Counts read remote-tracking refs as they stand on disk, so the human mode says so and `--fetch` refreshes first; `--fetch` without `--status` is rejected rather than silently ignored. `--json` adds `mode`, a `fetched` flag, per-repo `upstream`/`empty`/`error`, and a `summary` roll-up.
- **`inventory` accepts `--follow-symlinks`**, matching `plan`, `apply`, and `pushed`, now that its discovery goes through the same `find`-based walk.

- **Container discovery recognizes `Containerfile` and `<name>.compose.yml`.** The patterns matched `Dockerfile*` and four exact compose names, so a Podman-native repo was invisible to every sweep unless it happened to also carry a `Dockerfile` symlink — one such file had silently drifted to a stale base-image digest. Now matches `Containerfile`, `Containerfile.<suffix>`, `*.containerfile`, and `<name>.compose.yml`/`.yaml` alongside the existing names. A `.<suffix>` is still not inspected, so `Containerfile.md` qualifies exactly as `Dockerfile.md` always has.

### Fixed

- **`inventory` missed most of the repositories it was pointed at.** Project rows came from a fixed `root/org/project` directory walk that never looked for `.git`, so what counted as a project depended entirely on how deep a checkout happened to sit. On a host-nested tree (`<root>/<host>/<org>/<repo>`) an entire GitHub org collapsed into a single row that absorbed every manifest beneath it; checkouts nested deeper — submodules, in-tree packages — had no row at all; dot-named repositories such as `.github` were skipped as hidden directories; and plain directories that were not repositories became rows, including `node_modules`, because `SKIP_DIRS` was never applied at that level. Against the reference fleet the report went from 19 rows to 119. Rows are now git checkouts found by the same discovery `pushed` and `audit` already used, and each row's manifest walk prunes the nested checkouts that have rows of their own, so a superproject is no longer credited with its submodules' dependencies. `--flat` is unaffected.

- **Every ghcr.io digest refresh failed closed.** The registry token scope was taken from the `WWW-Authenticate` challenge on the `/v2/` probe, but that endpoint is not repo-specific, so a registry can only answer with a placeholder — ghcr.io returns a literal `repository:user/image:pull`. The token minted for that grants no access to the repo actually being queried, so the manifest HEAD 401'd and the pin was reported `[report-only] digest query failed` rather than refreshed. Scope is now always built from the repo being queried; only realm and service come from the challenge. docker.io was unaffected (it skips the probe entirely).
- **A container image bump was applied to the tree but never committed, and reported as a lockfile change.** `snapshot_workspace_files` and the commit-scope basename maps are keyed by manager, and `container` fell through to an empty set in both: the snapshot diff saw nothing, so the workspace's changes came from the post-apply fallback (which reports the directory's *lockfiles* — a container workspace at a repo root got a `uv.lock` entry), and `upgrade_scoped_paths` matched no Dockerfile or compose path, so `--commit` staged everything except the file that actually changed. Both now use the container module's own `find_container_files` discovery rather than a basename list. The scope also covers each file's resolved target, since a repo may ship `Dockerfile` as a symlink to `Containerfile` and the write through the link is what git reports as modified.

### Changed

- **BREAKING (JSON consumers): `inventory` row `name` values changed shape.** They were `<org>/<project>` directory pairs; they are now each checkout's path relative to its scan root (`com.github/Rethunk-Tech/repo-ops`), which is what makes a repo at any depth addressable and unambiguous. Anything parsing `name` as exactly two segments needs updating.
- **`--exclude` now unions with the config layers instead of replacing them.** `merge_exclude_patterns` preferred non-empty CLI patterns, and the global/per-repo merge in `run.py` picked one layer with an `elif` — so an ad-hoc `repo-ops apply --exclude foo` discarded the standing `exclude` list in `~/.config/repo-ops/config.toml` and swept every repo that list existed to protect. CLI, per-repo `exclude`, and global `exclude` are now unioned (first-seen order, deduped). Excluding a repo for one run still works; it just can no longer un-exclude the others.
- **Swallowed exceptions in the actionlint cache, the inventory counters and the org allowed-actions probe now name what they catch and log it at debug level.** Each was a bare `except Exception: pass`, so a systematically failing cache or an unreadable manifest looked identical to a clean scan. Behaviour is unchanged — these paths are still best-effort and still non-fatal — but the failure is now visible with `-vv`, and an unexpected exception type propagates instead of being counted as "nothing here".
- **`deptry` runs as a gate.** Imports must resolve from a declared dependency and declared dependencies must be imported; it is wired into `.gate.toml` since dependency hygiene is not something a project's manifests declare on their own.
- **Bundled agent skill ships `REFERENCE.md` instead of `AGENTS.md`.** Cursor auto-applies any `AGENTS.md` in a workspace, so installing the contributor guide into `~/.claude/skills/repo-ops` loaded release-cut and PyPI notes on every harness-editing session. `install-skill` now copies operator quick reference as `REFERENCE.md` and ignores `AGENTS.md`. Checkout `AGENTS.md` is unchanged.

## [0.23.0] - 2026-08-15

### Fixed

- **`audit --fix` could not pin an exact advisory-safe version for uv projects.** `apply_remediations` had dedicated branches for node, rust, deno and go, but `py-uv` fell through to the generic path — `uv lock --upgrade` followed by `uv sync`, a broad resolver-driven bump that may leave the vulnerable transitive dependency under-fixed while dragging every other dependency along with it. The targeted applier existed but was never wired to a call site; it now issues `uv add <package>==<version>` like the other ecosystems.
- **A broken per-manager registration silently disabled `audit --fix` for that manager.** The `ImportError` was swallowed into a `skip` outcome, so a remediation simply did not happen and nothing said so. The swallow remains (a legitimately absent optional dependency must not crash the run) but now logs a warning naming the manager.
- **Hold resolution could read the developer's own `~/.config/repo-ops/config.toml` during tests**, validating behaviour against real machine state rather than the fixture.

### Changed

- **BREAKING: Python 3.11 is no longer supported.** Minimum is now 3.12. Coverage only measures branches through `sys.monitoring` on 3.14 — below that it silently falls back to the C tracer, roughly doubling test execution — so 3.14 is the pinned interpreter and CI runs 3.12/3.13/3.14 with every version blocking (3.14 was previously advisory-only).

- **BREAKING: the 31 workspace distributions are now one `repo-ops` package.** The split shipped exactly one artifact and that artifact could not be installed: release built only `packages/repo-ops`, producing a 509-line wheel whose metadata required 24 sibling distributions that were never built or published, so `pip install repo-ops` failed to resolve `repo-ops-core`. The boundary was not real in the other direction either — `repo-ops-upgrade` and `repo-ops-upgrade-remediations-js` depended on each other at both the manifest and import level, which only worked because uv flattened the workspace into one environment. Every module keeps its name and import path (`repo_ops_core`, `repo_ops_upgrade`, …); they now live under `src/` and ship from a single manifest. Installing from a clone is `uv tool install .` and from git is the plain repository URL — the `#subdirectory=packages/repo-ops` suffix is gone.
- **BREAKING: Python 3.10 is no longer supported.** It reaches end of life in October, and it was the only reason for the `tomli` dependency and the config-loading fallbacks around it. Minimum is now 3.11, and `tomllib` is used directly.
- **`--sarif-out` and SARIF output are removed.** Nothing consumed them: no workflow in this repo or in `Rethunk-Tech/gh-actions` ever passed the flag, so the format was maintained for no reader. The JSON report and the GitHub step-summary writer are unaffected.
- **BREAKING: full-coverage sweep is now the default for `plan` and `apply`.** The recommended invocation required ~15 flags nobody could remember, and agents driving the CLI routinely omitted them, producing incomplete sweeps that read as complete. Every ecosystem now crosses ranges, every package manager gets its per-package targeted bump, and GitHub Actions/container/pre-commit/submodule pins get digest refresh, pin-to-SHA, and cross-major bumps — all with no flags. `--conservative` replaces all of them: stay in-range, only move what each manifest's own constraints already allow. Removed flags: `--latest`, `--full`, `--yarn-targeted-bump`, `--npm-targeted-bump`, `--pnpm-targeted-bump`, `--bun-targeted-bump`, `--deno-targeted-bump`, `--rust-targeted-bump`, `--poetry-targeted-bump`, `--pdm-targeted-bump`, `--pipenv-targeted-bump`, `--pip-tools-targeted-bump`, `--actions-refresh-sha`, `--actions-pin-sha`, `--precommit-refresh-sha`, `--submodule-refresh-sha`, `--container-update-latest`, `--container-refresh-sha`.
- **BREAKING: `--on-verify-fail` now defaults to `revert`** (was `keep`): a failed verify gate rolls back apply-artifact files (lockfiles and manifests) automatically, since the cross-range bulk upgrade a sweep performs has no per-package rollback of its own. `--on-verify-fail=keep` opts back into leaving the wreckage in place for inspection.
- **BREAKING: `--submodules` now defaults on** (detection/annotation only, no writes). `--no-submodules` opts back out.
- **BREAKING: `--submodule-order` now defaults to `children-first`** (was `discovery`), so a parent umbrella never bumps a pointer at a child this run hasn't updated yet. `--submodule-order=discovery` restores the old order.
- **BREAKING: `--migrate-to-uv-lock` now defaults on.** Hatch projects lacking a `uv.lock` get one instead of being silently skipped. `--no-migrate-to-uv-lock` opts back out.
- **BREAKING: `--only-outdated` now defaults on for human output.** JSON output stays complete and unfiltered regardless. `--no-only-outdated` opts back into listing every workspace scanned.
- **GitHub Actions org allowed-actions policy checking now runs automatically whenever `gh` is authenticated** (probed via `gh auth token`), annotating violating rows at the existing default `report` level; a host without an authenticated `gh` silently skips it. `--actions-org-policy` still forces it on.

### Removed

- **`--yarn-jobs`:** use `REPO_OPS_YARN_MAX_CONCURRENCY` instead (default raised from 2 to 4). Yarn extraction now targets disk-backed scratch (honors `TMPDIR`, else `/var/tmp`) rather than the default temp dir.
- **`scripts/set_version.py`:** it existed to hold ~20 manifests at one version, complete with drift detection and all-or-nothing validation. With a single manifest, `uv version --bump patch` does the same job.
- **`scripts/check_import_boundaries.py`:** a hand-rolled AST linter policing a package layering that no consumer could observe, and that no longer exists.

## [0.22.3] - 2026-08-15

### Fixed

- **`discover_repos` could never find a git submodule:** the underlying `find -type d -name .git` predicate structurally cannot match a submodule's `.git`, which is always a regular *file* (a gitlink pointing at `.git/modules/<name>`), never a directory. Every submodule in a monorepo was silently invisible to every scan/apply — `--submodules` only annotates parent-pointer-commit metadata and was never wired into repo discovery itself. `find` now matches `.git` as either a directory or a file. Verified against `Rethunk-Tech/Bastion`: repo count for the `actions` filter went from 3 (the top-level repo plus two submodules that happened to be reachable some other way) to the real 19.

## [0.22.2] - 2026-08-15

### Added

- **`--actions-pin-sha`:** converts a floating tag-pinned GitHub Action (`@vN`, `@vN.M.P`) to a commit-SHA pin, resolved to the highest `vN.M.P` release in the pin's current major (crossing majors under `--latest`, mirroring `--actions-refresh-sha`'s cross-major behavior), adding a `# vN.M.P` audit-hint comment or replacing any existing trailing comment. Neither `--actions-refresh-sha` nor `--actions-unpin-sha` touches an Action that was never SHA-pinned in the first place — both only govern already-SHA-pinned refs. Pure opt-in, not bundled into `--full`.
- **`--rust-targeted-bump`:** opt-in Cargo.toml constraint bumps for direct crates.io dependencies (including `[workspace.dependencies]`) whose latest non-yanked release is outside the current req. Snapshot/restore on `cargo update -p --precise` failure; holds stay in `behind_latest`. Not implied by `--latest` or `--full`.
- **`--poetry-targeted-bump` / `--pdm-targeted-bump`:** opt-in pyproject constraint bumps when a direct dep's lock is behind a published version that the current range cannot take. Reversible per-package `poetry add` / `pdm add`; holds stay in `behind_latest`. Not implied by `--latest` or `--full`.
- **`--pipenv-targeted-bump` / `--pip-tools-targeted-bump`:** opt-in Pipfile / `requirements.in` constraint bumps when a direct dep's lock is behind a published version that the current range cannot take. Reversible per-package `pipenv install` (existing venv only; never creates one) / `requirements.in` rewrite then compile; holds stay in `behind_latest`. Not implied by `--latest` or `--full`.
- **pnpm workspace catalogs:** plan/apply read `catalog:` and `catalogs:` in `pnpm-workspace.yaml`. With `--pnpm-targeted-bump`, catalog versions bump (hold ceilings and snapshot/restore on failed or ghost `pnpm install`); without it, catalog drift is `behind_latest`. `pnpm-workspace.yaml` is in apply commit scope. Protocol pins (`workspace:`, `file:`, …) are left alone; npm epoch pins (`1:2.3.4`) are versions.
- **`repo-ops doctor` probes `ruff`:** same found/missing/version row as the other Python toolchain binaries (`pyrefly`, hatch, pip-audit). `--strict` fails when ruff is absent. No `ruff format` probe.
- **SBOM GitHub Actions components include reusable workflows and nested actions:** `uses: owner/repo/.github/workflows/ci.yml@v1` and `owner/repo/subdir` are emitted with the full path as `name` and a `?subpath=` PURL qualifier so two workflows in one repo at the same ref do not collapse. Local `./`, `../`, and `docker://` uses stay omitted.
- **JSR license enrichment:** `--enrich-licenses` resolves `pkg:jsr` / ecosystem `jsr` from cached `https://jsr.io/@scope/name/<version>_meta.json`, then `jsr.json` / `deno.json`. Unscoped names and unrecognized SPDX stay unresolved. Offline `license` emits Deno `jsr:` rows as UNKNOWN so enrich can fill them; `http(s):` specifiers stay omitted.
- **Go license enrichment:** `--enrich-licenses` resolves `pkg:golang` / ecosystem `go`/`golang` from GOMODCACHE (`resolve_go_license`), same layout as `repo-ops license` go.sum scanning.
- **Hold-declined bumps are reported:** a package a hold keeps in `behind_latest` is now named on its report row (`(latest; held <= 6, until 2026-11-01)`), counted in the behind-latest header (`, 1 held`), and rolled up as `packages_held` in the run summary and JSON report. Output is byte-identical when no holds are configured.

### Fixed

- **Hold `ecosystem` filter silently matched nothing for JS workspaces:** `[[hold]]` rules documented `ecosystem = "js"`, but discovery labels every npm/yarn/pnpm/bun workspace `node`, so the filter matched no workspace, `resolve_hold` returned `None`, and the package was bumped past its `max_version` with no output at all. `resolve_hold` now normalizes `js`/`javascript` to `node` and `python` to `py`; an `ecosystem` value matching no known ecosystem or manager warns once on stderr rather than failing closed in silence.
- **pnpm catalog lock `from_ver` prefix-matched package names:** catalog lock lookup now splits `name@version` (including scoped names) and keeps a token only when the package name equals the requested name, so `react` does not pick `react-dom`.
- **Poetry/PDM targeted-bump `from_ver` was the pyproject constraint:** plan now reports the lock-resolved current; apply looks up `^`/`~` from the live manifest by package name.
- **Rust latest-direct cache keyed only mtime+size:** the cache key also includes a SHA-256 digest of `Cargo.toml` bytes so a same-size rewrite cannot reuse a stale result.
- **pnpm catalog `behind_latest` used the YAML spec as `from_ver`:** when `pnpm outdated` has a row, catalog packages now report the installed `current`, matching non-catalog pnpm rows. In-range `wanted` bumps stay in `changes` instead of being stripped. Missing-outdated catalog rows use the lockfile-resolved version when `pnpm-lock.yaml` is parseable; spec stripping uses `normalize_version` rather than `lstrip`.
- **`apply_rust` re-queried crates.io** after `check_rust` in the same process: latest-direct candidates are cached on workspace path, Cargo.toml mtime/size, and content digest.
- **Catalog pins missing from `pnpm outdated` were invisible:** with `--latest` or `--pnpm-targeted-bump`, `pnpm view` fills `behind_latest`; conservative plan does not hit the registry.
- **Catalog-only `pnpm-workspace.yaml` commits used the generic deps subject:** `classify_commit_subject` treats that basename as Node.
- **Python 3.10 TOML parsing crashed at `filesystem` import without pytest's tomli extra:** `repo-ops-core` now depends on `tomli` under `python_version < "3.11"`. When the parser is still missing, or when `tomli` raises `ImportError`, `read_toml_file` and config skip-and-warn instead of raising. Cargo license enrichment uses the same helper.
- **Hold clamp rewrote Python manifests:** uv/hatch clamp with `uv lock --upgrade-package <pkg><=ceiling>` (lock only). poetry/pdm/pipenv still run add/install then restore `pyproject.toml` / `Pipfile` bytes. Audit `--fix` still uses `uv add pkg==safe`. pip-tools remains constraints-file-only.
- **Deno `apply` hard-failed ghost bumps before targeted-bump retry:** with `--deno-targeted-bump`, unresolved planned targets fold into `behind` and retry, matching npm. Without the flag, ghost still raises.
- **Deno hold/release ignored `jsr:` import-map pins and treated `npm:`/`jsr:` tails as the same package:** prefix match is registry-aware; unprefixed names still match their registry form.
- **Bun remediations invoked package.json scripts instead of package binaries:** `pm_exec_prefix("bun")` returned `["bun", "run"]`, so remediations like `biome migrate --write` became `bun run biome migrate --write` — running the `biome` script and appending args (e.g. `biome check --write . migrate --write`). Now uses `bunx` to execute installed binaries directly, matching yarn/pnpm/npm exec semantics.
- **`inventory` undercounted declared Python/Rust deps:** `direct` unions Cargo `[workspace.dependencies]` with package dependency tables (same name once), and counts Pipfile `[packages]`/`[dev-packages]`, `[tool.pdm.dev-dependencies]` groups, and `requirements.in`. uv.lock / poetry.lock versions use the shared TOML parsers instead of a line scanner.
- **Dependabot `--scaffold` mapped `pnpm-lock.yaml` to `npm`:** that lockfile now emits `package-ecosystem: pnpm`. Compose detection includes `docker-compose.yaml`, `compose.yml`, and `compose.yaml` as well as `docker-compose.yml`.
- **JSR enrichment returned `None` when `_meta.json` had a present-but-empty `license`:** a non-normalizing meta license falls through to `jsr.json` / `deno.json`.
- **`license` dropped poetry.lock packages with no version:** named rows still appear with version `?`, matching uv.lock.
- **`license` skipped `deno.lock` `jsr:` specifiers:** they are scanned as UNKNOWN and filled when `--enrich-licenses` is on.
- **`versions_deno_lock` omitted v3 `packages.jsr`:** keys are emitted as `jsr:@scope/pkg@version`, matching the top-level `jsr` section.
- **Dependabot never high-confidence-resolved `deno.lock`:** npm: and jsr: entries participate in supersession like other lock parsers.
- **Inventory `edges` ignored `pdm.lock` / `Pipfile.lock`:** those locks count resolved packages before falling back to manifests. Pinned `requirements.txt` / `-dev` / `-test` fill `versions` when no Python lock exists.
- **`classify_commit_subject` missed `Cargo.lock`, `pdm.lock`, `Pipfile.lock`, `requirements.in`, and Deno lockfiles:** those basenames get Rust / Python / Deno subjects instead of the generic deps line.

### Changed

- **Shared targeted-bump requirement matcher:** Poetry, PDM, Cargo, Pipenv, and pip-tools use `repo_ops_core.requirement.requirement_satisfied` with an explicit default operator (`^` for Poetry/PDM/Cargo, `==` for Pipenv/pip-tools). Cargo stays on this helper rather than PEP 440 `SpecifierSet` because Cargo reqs are Cargo semver (caret is not PEP 440).
- **CycloneDX SBOM default is spec 1.5:** `metadata.tools` is an object with `components[]` (`type`, `publisher`, `authors[]`, `name`, `version`). The 1.4 `tools[]` `{vendor,name,version}` shape and the 1.4-style `author` string are dropped; there is no 1.4 opt-in flag.
- **Go hold ceilings use shared `hold_ceiling_spec`:** `go get` queries stay `<=v` plus the same three-component pad as Python managers.
- **Dependabot JSON summary:** renamed `summary.excluded_repos` to the fleet-canonical `summary.excluded`; consumers must migrate to the new key.
- **SBOM lock collection uses shared parsers:** uv.lock, poetry.lock, go.sum, Cargo.lock, and `requirements.txt` / `-dev` / `-test` go through `repo_ops_lockfiles.parsers`. Inventory reuses the Cargo.lock and requirements parsers.

## [0.22.1] - 2026-07-28

### Fixed

- **Every bun `apply` logged a bogus `WARN corepack prepare bun@x.y.z --activate failed:` line:** `corepack_prepare` ran for all Node managers, but Corepack ships shims only for npm, pnpm, and yarn — a bun `packageManager` pin is an outright `Unsupported package manager specification` Usage Error. Corepack is now skipped entirely for managers it does not support (bun today), including the `corepack enable` call; npm/pnpm/yarn still prepare exactly as before. Purely log noise: the upgrade itself always used the real `bun` on PATH. A genuine prepare/enable failure now also falls back to stdout when stderr is empty, so the warn carries Corepack's actual message instead of trailing off blank (Corepack prints Usage Errors on stdout).

## [0.22.0] - 2026-07-25

### Added

- **Targeted bump releases legacy (unheld) `resolutions`/`overrides`/`pnpm.overrides` pins:** every `--*-targeted-bump` retry loop (yarn/npm/pnpm/bun) now releases a manifest-level pin for the package being bumped when no active `[[hold]]` rule covers it, before running the install command — a naked pin predates the hold feature (0.21.0) or was a one-off fix never promoted to a tracked hold, and treating it as a permanent silent hard stop just accumulates dependency debt. An active hold is left completely untouched (still respected via its ceiling). Safe by construction: the existing snapshot/revert-on-no-advance path in every `targeted_bump_behind` reverts the pin edit too if the retry doesn't actually land. See `repo_ops_js.overrides.release_unheld_pin`.

### Fixed

- **`--yarn-targeted-bump` / `--bun-targeted-bump` never got a chance to run against a resolutions/overrides-blocked package (#121 follow-up):** yarn's and bun's ghost-bump guard fired immediately after the first bulk update pass and raised, failing the whole workspace before the per-package targeted-bump retry loop ever executed — so enabling targeted-bump did not actually help the exact case it exists for. The initial guard now defers a blocked target to the targeted-bump retry (which can also release an unheld pin, see above) instead of hard-failing; a target still unresolved after that best-effort retry is reported via the normal `behind_latest` path, same as any other targeted-bump miss. `guards.assert_targets_resolved` is unchanged for callers with no fallback (targeted-bump disabled) — a ghost bump there still raises loudly, as it should.

## [0.21.0] - 2026-07-25

### Added

- **Package version-ceiling ("hold") rules:** a `[[hold]]` config rule (global `~/.config/repo-ops/config.toml`, or per-repo `pyproject.toml`'s `[tool.repo-ops]` table / `.repo-ops.toml`) caps a specific package at a maximum version, optionally until an expiry date, with optional workspace-scoped overrides (a per-workspace rule narrows or overrides a repo/global one for one monorepo member). Lets an operator hold back a specific major upgrade the ecosystem isn't ready for (e.g. TypeScript 7) without disabling `--latest` / `--*-targeted-bump` for every other package in the repo. Enforced across npm, yarn, pnpm, bun, Go modules (same-major minor/patch ceiling only — Go's own resolver never crosses a major), and the Python managers uv, hatch, pdm, pipenv, poetry, and pip-tools (the last enforced via an ephemeral pip constraints file passed to compile, never writing into `requirements.in`). Audit `--fix` advisory-safe version overrides always win over an active hold — a security remediation is never silently capped by a routine hold policy. Config-file only, no CLI flag or env var (same reasoning as `exclude_patterns`). See [docs/usage.md — Package holds](docs/usage.md#holds).

### Fixed

- **`workflow_roundtrip_text` reformatted block-sequence indentation on a no-op load+dump:** the ruamel.yaml round-trip loader used for workflow-YAML drift checks wasn't configured with GitHub Actions' conventional sequence indent style (dash offset two spaces past the parent key), so a no-op load+dump reindented every `- item` line in a file using that style — caught by this repo's own `ci.yml` after an unrelated Action-pin bump. The actual `--actions-*` pin-rewrite path was never affected (it already used byte-preserving targeted line edits, not a full re-dump).

### Changed

- **Deps:** routine Python dependency refresh (`uv.lock`) and `astral-sh/setup-uv` Action bump (8.3.2 → 9.0.0), plus the ruff findings (`S310`, `RUF036`) the latter surfaced.

## [0.20.4] - 2026-07-21

### Fixed

- **`verify` fast-path silently skipped the type-check gate for `pyrefly`-only repos (#155):** `verify.py`'s dev-deps heuristic only recognized `mypy`; repos already standardized on `pyrefly` (bakeoff, repo-ops itself, Albinogeek/Personal/tools, sagaforge, paper-trail) got no type-check gate at all instead of a failure. Added a matching `pyrefly` branch (`uv run pyrefly check .`), checked after `mypy` so a repo carrying both keeps the `mypy` gate.

### Changed

- **Dev tooling:** replaced `mypy` with `pyrefly` for type checking across the workspace (CI, release gate, docs); repository-only change, no user-facing effect.
- **Test suite:** cleared the last leaked `pytest` warning (an intentionally-triggered one-time `UserWarning` for the tomllib-unavailable path is now asserted with `pytest.warns` instead of leaking) and fixed several `tests/test_audit*.py` fixtures that mistyped the `tmp_path` fixture as `pytest.TempPathFactory` (the type of the separate `tmp_path_factory` fixture) instead of `Path`; both were caught by an unscoped local `pyrefly check .` run, though neither affected the actual CI/release gate (`--project-excludes tests/** scripts/**`).

## [0.20.3] - 2026-07-21

### Fixed

- **`apply --commit` remediation-written files dropped from root-workspace commits (#154):** a root-scanned workspace's applied remediation (e.g. `biome_schema` rewriting `biome.json`'s `$schema` URL after a `@biomejs/biome` bump) is now staged alongside the lockfile — previously it was silently dropped, since the basename-only root match only ever considered the package manager's own manifest/lockfile. `upgrade_scoped_paths()` now unions in each applied remediation hook's own declared `files`, so this also covers future remediations that write a root-level file outside the manager's manifest/lockfile.

## [0.20.2] - 2026-07-21

Note: `v0.20.1` was tagged but its release build failed the `ruff format --check` gate before publishing any artifacts; superseded immediately by this version. Do not use `v0.20.1`.

### Fixed

- **`apply` sibling-workspace dirty-skip false positive (#152):** the dirty-worktree check is now snapshotted once per repo bundle before any of its workspaces are processed, instead of re-checked live before each workspace — an earlier workspace's own already-applied (not yet committed) changes in the same repo/run no longer make every later sibling workspace look dirty and get skipped outright.
- **`apply --commit` nested yarn-workspace manifest dropped (#153):** when the scanned workspace is a monorepo root (`rel == "."`), a nested workspace member's `package.json` touched as a side effect of resolving a peer/group-constrained bump elsewhere in the tree is now staged and committed alongside the root lockfile, scoped to the root's own declared `workspaces` globs — previously it was silently dropped, leaving the lockfile and manifest out of sync on `main`.

### Changed

- **Deps:** routine Python dependency refresh (`uv.lock`).

## [0.20.0] - 2026-07-19

### Added

- **`dependabot` uv.lock high-confidence parser (#150):** `uv.lock` `[[package]]` entries feed the supersession check like npm/yarn/pnpm/bun and go.mod/Cargo.lock, so already-resolved Python-ecosystem PRs are detected (and auto-closable) instead of falling through to the never-auto-closed text-search fallback; PR-title package names are PEP 503-normalized before lookup.

### Fixed

- **`container-refresh-sha` multi-stage digest drift (#151):** every `FROM ...@sha256:...` occurrence is now checked and refreshed independently instead of deduping by image:tag — a stage that already carries the current digest no longer shadows a sibling stage with a stale one, and apply patches every line with an outdated ref (registry digest lookups remain deduped per image:tag).
- **Bundled skill AGENTS.md** re-synced with the root `AGENTS.md` `--latest` coverage-rule trim.

### Changed

- **AGENTS.md:** recommends the full-coverage sweep flag combo (`--latest --actions-refresh-sha --container-refresh-sha --*-targeted-bump`) by default for bulk maintenance, since bare `--latest` under-reports drift.
- **Deps/CI:** routine Python dependency and CI action pin refresh.

## [0.19.0] - 2026-07-18

### Added

- **`repo-ops-py-pdm` / `repo-ops-py-pipenv`:** first-class Python PM plugin packages with `register()`, workspace/CI/mypy/coverage wiring, and smoke tests (compat re-exports remain under `repo_ops_py`).
- **npm/pnpm/yarn apply-level audit overrides:** advisory-safe version pins apply like bun (#140 parity), including when check reports no other changes; pnpm targeted-bump restores the lockfile on ghost-bump guard failure.
- **Deno `behind_latest` + `--latest`:** Deno check reports behind-latest gaps; `REPO_OPS_DENO_UPDATE_LATEST` flips under the fleet `--latest` umbrella; ghost-bump guard after apply.
- **`audit --fix-plan` documentation** and cheat-sheet coverage; doctor probes `cargo-audit` via `cargo audit --version`.
- **Inventory `bun.lockb`:** best-effort `bun` dump to yarn-compatible text, or explicit `bun(bin:unsupported)` instead of silent zero versions.
- **License/SBOM breadth:** poetry/pdm/pipenv lock scans; npm lockfile fallback without `node_modules`; pdm/pipenv/deno snapshots; bun.lockb / requirements.txt / jsr: in SBOM; PyPI metadata (readme/authors/classifiers/urls) on the publishable `repo-ops` wheel.
- **Verify/commit coverage:** pdm/pipenv verify gates; hatch verify skips `uv sync --locked` when no `uv.lock`; pdm/pipenv commit scoping.
- **CI:** blocking pytest excludes `integration`; advisory integration job installs govulncheck + Node/Corepack; audit package tests and pdm/pipenv smokes collected; remediations + container/audit/dependabot/doctor on mypy; stronger release.yml gates; `repo_ops_container` / pdm / pipenv import-boundary rules.
- **install-skill:** rewrites relative `HUMANS.md` / `docs/*` / `packages/*` links to GitHub blob URLs on install.

### Fixed

- **Hatch plan/apply alignment:** without `uv.lock` and without `--migrate-to-uv-lock`, check no longer reports actionable drift (apply was already a no-op).
- **`go_generate` remediations:** default-off on the production `dispatch` path unless `REPO_OPS_REMEDIATE_GO_GENERATE=1` (closes env-gate bypass).
- **Timeouts:** `find_timeout` / SETTINGS defaults unified at **60s**; docs reconciled.
- **Container Hub auth:** `credHelpers` resolves `registry-1.docker.io` aliases.
- **Fail-fast pool:** drain the completed `wait()` batch before clearing futures (no KeyError / dropped results).
- **Docs/CLI:** CONTRIBUTING MCP server id `plugin-rethunk-git-git`; usage/AGENTS/testing/architecture/programmatic-imports pass for audit flags, Rust `--latest` honesty, and workspace export surface.
- **Tests:** pytest module basename collision for go env classify; pnpm ghost-bump integration; orphaned `packages/repo-ops-audit/tests` now in `testpaths`.

### Changed

- **Yarn targeted bump:** skip ghost-bump isolated-copy extraction when the workspace has no `resolutions` entries (perf; no behavior change when resolutions exist).
- **Core plugin registration:** pdm/pipenv/deno/precommit registration lives with the meta package / plugins rather than core-only paths where applicable.
- **Coverage/mypy:** remediations-{core,py,go,rust,deno} included in workspace gates.

## [0.18.1] - 2026-07-14

### Fixed

- **`apply`/`plan` `--json`/`--json-out` no longer drops co-located non-container workspaces:** result reconciliation keyed only by path, so a Dockerfile next to `package.json` (or any other manifest) overwrote the earlier ecosystem's result with the container one. Keys are now `(path, manager)`. (#146)

### Changed

- **`[done]` progress lines include per-repo duration** (`in 1.2s` / `in 2m 6s`) so slow workspaces are glanceable during plan/apply runs. JSON `elapsed_s` was already present.

## [0.18.0] - 2026-07-10

### Added

- **`repo-ops doctor`:** read-only toolchain/auth preflight — probes the ~19 binaries the plugins shell out to (resolved path + version, grouped by ecosystem) and `gh auth status`, so missing tools surface up front instead of as silent per-repo skips. `--strict` exits 1 on any missing binary; `--json`/`--json-out`/`--compact` follow the existing conventions.
- **Staleness scoring:** plans and applies persist per-repo behind-latest history in the state store and report `staleness_score` (behind-latest count × days since the repo first fell behind) and `new_behind_since_last_run` in JSON, plus a top-3 staleness footer line; entries self-prune after two clean runs, and `REPO_OPS_NO_STATE=1` disables persistence.
- **`--latest` umbrella now covers all cross-range gates:** container image, pre-commit hook, and submodule tag cross-major upgrades (`REPO_OPS_CONTAINER_UPDATE_LATEST`, `REPO_OPS_PRECOMMIT_UPDATE_LATEST`, `REPO_OPS_SUBMODULE_UPDATE_LATEST`) are flipped by `--latest` instead of being reachable only via raw env/config.
- **`--bun-targeted-bump`:** bun gains the same behind-latest targeted-bump fallback yarn/npm/pnpm already had (`bun add <pkg>@<version>` with lockfile snapshot/restore); `apply_bun` no longer discards the behind-latest list it computed. Configurable as `bun_targeted_bump` in `[tool.repo-ops]`.
- **`audit --fix` remediates Go:** fixed versions are extracted from govulncheck findings and applied via `go get <module>@<fixed>` + `go mod tidy`, mirroring the cargo path; graceful skip when `go` is absent. Go was the last scan-only audit ecosystem.
- **audit fail gates are config-readable:** `audit_fail_on` / `audit_fail_on_unknown` in `[tool.repo-ops]` (env: `REPO_OPS_AUDIT_FAIL_ON`, `REPO_OPS_AUDIT_FAIL_ON_UNKNOWN`) with CLI > env > config > default precedence.
- **`license` resolves Go/Rust/Deno:** licenses are read offline from local caches (GOMODCACHE LICENSE classification, `cargo metadata`, DENO_DIR npm cache); ecosystems with no extractor (container) are reported unknown but no longer force `--fail-unknown` exit 2.
- **`sbom` emits pin components:** container image pins (`pkg:oci`) and GitHub Actions `uses:` pins (`pkg:github`) now appear in both CycloneDX and SPDX output, reusing the existing parsers.
- **`inventory` counts submodule pins:** git submodule gitlinks surface as a new `submodule` ecosystem alongside container/actions/pre-commit pins, resolved locally from `.gitmodules` + `git ls-tree`.
- **`deno fmt` post-upgrade remediation hook** (`repo-ops-upgrade-remediations-deno`): scoped to changed files, graceful skip when deno is absent, wired into orchestration like the go/js/py/rust hooks.

### Fixed

- **npm/pnpm behind-latest hints are targeted-bump-aware**, matching yarn (#131 parity): once the targeted-bump flag was passed, the hint reports the fallback was attempted instead of re-suggesting a flag or claiming "no latest mode" applies; bun joins the same map.
- **Targeted-bump ghost-version guard (bun/npm):** when `assert_targets_resolved` detects a ghost bump after an apparently successful install, the workspace snapshot is now restored and the package returns to the still-behind list instead of the exception leaving a corrupted lockfile.
- **Stale `container` module docstring** claimed credential helpers and ECR auth were report-only; both shipped in 0.17.2 — the docstring now describes the credential-helper/ECR flow.

### Changed

- **Docs describe the full 9-subcommand surface:** `audit`, `dependabot`, `sbom`, and `license` added to README/AGENTS/usage tables and the CLI description; `sbom`/`license` gained usage sections; audit and install-skill flag tables corrected; the orphaned architecture doc is linked from HUMANS; `--json`/`--json-out`/DEFAULT_ROOTS scope claims qualified; the bundled skill AGENTS.md re-synced.
- **Import-boundary checker supports narrow allowlists:** `repo_ops_lockfiles` may import only `repo_ops_upgrade.workflow_uses` (for the sbom Actions-pin reuse) instead of a blanket package ban.
- **Coverage:** new targeted tests for remediations-js detect/report logic, core process verbose/error paths, uv-lock check parsing, dependabot URL/gh-absent/lockfile-scan helpers, poetry tool-absent/update parsing, the bun JSON parser and audit override helpers, the license CLI entrypoint and new extractors, sbom pins, inventory deno/flat/submodule paths, and the `--latest` umbrella/hint parity. `REPO_OPS_*` env vars are now isolated between tests via an autouse fixture.

## [0.17.3] - 2026-07-01

### Security

- **Submodule `.gitmodules` URL command injection:** an untrusted `url` field from a scanned repo's `.gitmodules` reached `git ls-remote`/`git fetch` with no `--` separator and no transport restriction, letting a malicious `ext::` URL execute arbitrary commands or a leading-dash URL inject options. Both call sites now insert a `--` separator before the URL and set `GIT_ALLOW_PROTOCOL=https:ssh`.

### Fixed

- **`audit --fix` now actually patches bun workspaces:** bun had no advisory-safe-version override mechanism (unlike npm/pnpm/yarn) and no per-package remediation extraction from `bun audit --json`, so `--fix` silently no-opped for bun-managed repos while reporting success.
- **CVSS scoring corrected to the full CVSS v3.1 base-score formula:** the previous approximation averaged only the Confidentiality/Integrity/Availability impact letters, under-scoring exploitable-but-narrow-impact vulnerabilities (e.g. a real 7.5 scored ~3.3) — enough to let a High-severity finding pass `--fail-on high` undetected.
- **`repo-ops dependabot` returns exit code 1 on unexpected errors**, matching its own documented exit-code contract (previously every code path returned 0, even on an unhandled `gh` failure).
- **license-policy SPDX compound-expression parsing:** `--deny`/`--allow` now correctly evaluate `OR`/`AND` SPDX expressions (e.g. `GPL-2.0-only OR MIT` no longer false-positives against `--deny GPL`) instead of a flat substring match.
- **`sbom`:** `uv.lock` parsing replaced with a real TOML parse (the previous line-scanner misattributed name/version across nested `[[package.dependencies]]` sub-tables); scan now applies the same exclude-dir filtering as discovery, so home-dir roots no longer pull vendored/cached lockfiles (`~/.cargo/registry`, `~/go/pkg/mod`) into the SBOM; `--output` writes are now atomic.
- **`container`:** a tag bump could false-match a longer tag sharing the same prefix (e.g. bumping `image:1` firing on a line pinned to `image:1.5`); replacement now requires a tag boundary. Also removed three exception types made dead by an already-present trailing `except Exception`.
- **`dependabot`:** default-branch resolution failure no longer silently assumes `"main"` in close comments (wrong on master/trunk repos); `--write`/`--json`/`--close` now validate against `--scaffold` instead of silently no-opping on an invalid combination; trailing-slash remote URLs now parse correctly; removed an unreachable grouped-PR guard.
- **`inventory`:** Go module cache path corrected from a legacy `~/.cache/dep_count/` to the shared `~/.cache/repo-ops/`; cross-package private-symbol imports now fail loudly (`AttributeError`) instead of being silently swallowed by a too-narrow `except ImportError` if a symbol is renamed.
- **`pip-tools`:** a failing `uv pip compile` with no `pip-compile` fallback available now raises instead of silently reporting no changes, matching the existing `pip-compile`-failure behavior.
- **`pushed`:** `--hours` is now silently ignored no more — a warning is printed when `--since`+`--until` are both provided and drop it.
- **audit SARIF output:** `artifactLocation.uri` is now repo-relative with a matching `run.originalUriBaseIds` entry (was an absolute host path, which does not resolve under GitHub code-scanning ingestion despite the "upload-ready" claim); removed an unreachable branch in `_probe_pip_audit`; `audit` subcommand labeling corrected to "(read-only unless --fix)" everywhere it appears.
- **models:** `RepoBundle.any_dirty_skip()` and its two producers (`orchestration.py`, `report.py`) now share one `DIRTY_SKIP_MARKER` constant instead of three independently-hardcoded copies of the same literal.

### Changed

- Coverage added for previously-uncovered branches in `commit_agent_cursor.py`, `pushed/audit.py` CLI flag combinations, and `pip_tools.py`'s apply path.

## [0.17.2] - 2026-06-30

### Added

- **pre-commit `rev:` pin ecosystem:** detects `.pre-commit-config.yaml` / `.pre-commit-config.yml`, queries upstream tags via `git ls-remote`, plans and applies conservative same-major bumps; SHA-pinned and non-semver revs are report-only; opt-in cross-major via `REPO_OPS_PRECOMMIT_UPDATE_LATEST=1`; `precommit` is now a first-class ecosystem in discovery and the upgrade registry.
- **`--fix-plan` flag for `repo-ops audit`:** prints a remediation checklist of actionable steps per vulnerability finding so agents and operators know exactly which packages to bump.
- **Container credential-helper + ECR auth:** private ECR registries are now accessible via the Docker credential-helper protocol and AWS ECR token exchange, removing the report-only fallback for authenticated registries.
- **PR/branch-based apply path (`--pr` / `--pr-base` / `--branch-prefix`):** after a successful apply+commit, creates a branch, pushes, and opens a PR via `gh` instead of direct-committing to the base branch; idempotent on an existing open PR for the same branch.
- **Multi-ecosystem remediation framework:** new `repo-ops-upgrade-remediations-core` provides an ecosystem-agnostic hook registry and dispatch layer; existing JS hooks are ported onto it; new Python (`ruff --fix`/format), Go (`gofmt`, `go generate`), and Rust (`cargo fmt`) remediation hooks added; `go generate` is opt-in behind `REPO_OPS_REMEDIATE_GO_GENERATE=1` (default off, security); formatters operate only on changed files; a missing formatter tool skips gracefully instead of failing the apply.
- **`audit --fix`:** applies advisory-safe remediations by delegating to the upgrade orchestration layer; also adds SARIF 2.1.0 output (`--sarif-out`) and a GitHub step-summary writer for native code-scanning integration and per-PR step summaries.
- **`sbom` subcommand:** emits a CycloneDX 1.4 or SPDX 2.3 SBOM (`--format cyclonedx|spdx`) from lockfile-resolved dependencies across npm, Python, Go, Rust, and Deno; writes to stdout or `--output PATH`, `--compact` for single-line JSON. Read-only — never writes lockfiles.
- **`license` subcommand:** scans dependency licenses and flags violations against an allow/deny list (`--allow` / `--deny` of comma-separated SPDX identifiers); unknown licenses exit 2 by default (`--no-fail-unknown` to override), `--json` for machine output. Read-only.
- **`dependabot` subcommand:** lists open Dependabot PRs and identifies those superseded by the current lockfiles/manifests on the default branch (`--close` actually closes them via `gh pr close`; dry-run by default); `--scaffold` (with `--write`) detects each repo's ecosystems and generates/regenerates `.github/dependabot.yml`, `--schedule` sets the update interval.
- **New ecosystems — PDM, pipenv, and Deno:** discovery, check, apply, and upgrade-registry registration for `pdm.lock` (PDM), `Pipfile.lock` (pipenv), and `deno.json`/`deno.lock` (Deno); Deno v4/v5 lockfile format (Deno 2.0+) parsed correctly; Pipfile-only repos surface an accurate skip reason instead of a false-green.
- **Submodule tag-advancement:** detects and applies advancing a submodule pointer to a newer upstream tag; opt-in cross-major via `REPO_OPS_SUBMODULE_UPDATE_LATEST=1`; dirty guard prevents advancing a submodule with uncommitted local changes; gitlink committed in scope after apply.
- **Apply resume/checkpoint (`--resume` / `REPO_OPS_RESUME=1`):** skips already-committed repos on a re-run after an interrupted fleet apply; checkpoints are scope-fingerprinted so a changed `--filter` or root set starts fresh rather than replaying a stale checkpoint.
- **`--log-level` flag:** controls run-time verbosity (`debug`/`info`/`warning`/`error`).
- **`--filter` expanded and `inventory` pin counts:** `--filter` now accepts `container`, `precommit`, `actions`, and `deno` in addition to existing package-manager choices; `inventory` now surfaces container, actions, pre-commit, and deno pin counts.
- **Config-file: five `REPO_OPS_*` env gates now readable from `[tool.repo-ops]`:** `REPO_OPS_PRECOMMIT_UPDATE_LATEST`, `REPO_OPS_CONTAINER_UPDATE_LATEST`, `REPO_OPS_REMEDIATE_GO_GENERATE`, `REPO_OPS_RESUME`, and related gates follow CLI flag > env var > config file > default precedence.

### Changed

- **`--on-verify-fail=revert` is artifact-scoped:** revert only touches apply-artifact files (lockfiles and manifests) written during this run; pre-existing dirty edits and untracked files outside that set are preserved.
- **Config-file layer wired into product run (`apply_config_to_env`):** `[tool.repo-ops]` keys (e.g. `go_upgrade_mode`, `jobs`) now reach the runtime environment with correct precedence (CLI flag > env var > config file > default).
- **Actions pool workers are isolated:** `_scan_one_bundle_actions` is wrapped in a try/except so unexpected errors (OSError, etc.) surface per-bundle instead of crashing the pool.
- **`bun audit` switched to `--json`** with ANSI text fallback for structured severity parsing; removes fragile regex on coloured output.
- **Coverage floor raised from 63 → 68** in `pyproject.toml`, `ci.yml`, and `release.yml`; release gate now enforces the same threshold.
- **`--actions-keep-sha` flag removed:** the flag was a dead stub; cross-major SHA re-pin (#138) is controlled by `--actions-upgrade-mode=latest` + the existing `--actions-refresh-sha` flag.
- **CLI flag docs:** AGENTS.md cheat sheet updated with previously undocumented flags (`--on-verify-fail`, `--actions-refresh-sha`, `--fix-plan`, etc.).
- **Config-file `exclude` and `git_timeout` keys now honored:** both keys were parsed by the config reader but not applied during product runs; they now reach the runtime with correct precedence alongside the other wired keys.

### Fixed

- **actionlint shellcheck warnings no longer fail the run:** actionlint findings were treated as a single binary pass/fail on the process exit code, so a cosmetic shellcheck warning (e.g. `SC2034:warning` "unused variable") was promoted to a fatal entry under `Failures:` and forced exit code 1. Findings are now classified per-severity — only error-class findings (native actionlint errors, shellcheck `:error:`) under `lint_level=block` are fatal; warning/info/style shellcheck findings (and all of `lint_level=warn`) become non-fatal `warn` rows and pin bumps proceed. Unrecognized output stays fatal (conservative default).
- **Generated Wails `wailsjs/` bindings dirs are skipped:** `wailsjs` is now in `SKIP_DIRS`, so generated Wails JS-binding stubs (a `package.json` with no lockfile / no `packageManager`, not a workspace member) no longer surface as node discovery skips — matching the existing pruning of Wails' `build`/`dist`/`out`.
- **Not-actionable discovery skips de-emphasized:** manifests with nothing to upgrade (no lockfile / no package manager / outside a git repo — e.g. Capacitor mobile subprojects) are reframed from a `Discovery skips` block beneath `Failures:` into an explicitly informational `Skipped (not actionable — informational, no action needed)` section, so a clean run no longer reads as if it had warnings. They were never counted in `failure_count`.
- **`audit --fix` pins the exact advisory-safe version, not `@latest`:** the JS fix path now performs a targeted bump to the minimum non-vulnerable version named in the advisory (per ecosystem) instead of over-bumping the dependency to `@latest`, so a security fix no longer drags in unrelated major upgrades.
- **Cross-major Actions SHA re-pin (#138):** `--actions-refresh-sha` now supersedes Dependabot SHA-pin PRs when combined with `--latest`; regression tests anchoring SHA-specific advancement to digest added.
- **Revert `git checkout` path guarded by before-snapshot set:** the revert path now only checks out files that were absent from the pre-apply tracked set, preventing inadvertent rollback of pre-existing dirty manifest-basename files.
- **Config-test env leak eliminated:** `apply_config_to_env` tests now isolate env mutations so container-ecosystem tests are no longer polluted by stale `REPO_OPS_*` values from a prior test in the same process.
- **Submodule apply correctness:** gitlink paths included in the post-apply commit scope; dirty guard and `--detach` checkout wired into the submodule apply flow (#18).
- **Pipfile-only repos surface an accurate skip reason** rather than a false-green "no upgrades" when no recognized lockfile is present alongside the `Pipfile`.
- **Remediations edge cases:** `FileNotFoundError` from a missing tool is treated as a graceful skip rather than a hard fail; `cargo fmt` and `gofmt` scoped to changed files only; `go generate` prunes skip directories.
- **Deno lockfile v4/v5 (Deno 2.0+):** `versions_deno_lock` now parses the v4/v5 format alongside the legacy format.
- **PDM, pipenv, and deno wired into CLI filters:** added to `MANAGER_FILTER_CHOICES` and `ECO_FILTER_CHOICES`; `manifest_kinds()` extended to detect deno manifests; pipenv checker no longer creates a venv as a side effect of the check phase.

## [0.17.1] - 2026-06-19

### Fixed

- **Yarn extraction concurrency cap (`--yarn-jobs` / `REPO_OPS_YARN_MAX_CONCURRENCY`, default 2):** the workspace `--jobs` pool could launch one Yarn Berry isolated-copy extraction per worker, each unpacking its cache into `/tmp` — on large monorepos this exhausted the disk (`ENOSPC`) and failed otherwise-valid yarn checks. Concurrent yarn extractions are now bounded by a process-wide semaphore, independently of `--jobs`, so non-yarn ecosystems keep full parallelism.

## [0.17.0] - 2026-06-19

### Added

- **`--migrate-to-uv-lock` flag (#132):** hatch projects that lack a `uv.lock` are no longer silently converted to uv-managed on apply — `apply_hatch` no-ops unless this flag opts in to creating the lockfile.

### Changed

- **`--npm-targeted-bump` / `--pnpm-targeted-bump` now take effect (#131):** both flags were advertised ("Sets `REPO_OPS_*_TARGETED_BUMP`") but never wired into their env vars, so they were silent no-ops; `run` now populates them alongside `--yarn-targeted-bump`.
- **Behind-latest hint is targeted-bump-aware (#131):** once `--yarn-targeted-bump` was requested, the per-line/footer note reports the fallback was attempted and could not move the leftovers, instead of re-suggesting the flag the operator already passed.
- **`requirements.txt`-only repos reported as an unactionable skip (#133):** a repo with no `requirements.in` is recorded as a discovery skip with a reason rather than a false-green "no upgrades".
- **uv-lock check/apply deduplicated (#134):** shared logic moved to `repo_ops_py.uv_lock`; `repo-ops-py-uv` and `repo-ops-py-hatch` both delegate to it.
- **Timeout resolvers relocated to `repo_ops_core.constants` (#136):** removes a base-package-imports-orchestration inversion; `repo_ops_upgrade.constants` re-exports for back-compat.
- **`pushed` reflog lookup is O(refs), not O(pushes×refs) (#135):** `_reflog_pre_sha` reuses a single per-ref reflog fetch; output is byte-identical.

### Fixed

- **Codebase audit batch** (correctness fixes across all ecosystems): config TOML loads on Python 3.10 (`tomli` fallback); the no-`tomllib` warning fires once; first-run state cache is thread-safe under the worker pool; common-dir dedup keeps the first-seen path; `install-skill --dry-run` exits 0 on an existing target; `verify.skip=true` no longer marks a workspace failed; per-repo `verify.timeout` is honored for hatch/pip-tools/poetry/`go mod verify`; remediations are skipped when an apply already failed; commit scoping never sweeps a pre-dirty workflow; the commit-skip reason carries the real verify error.
- **JS:** `npm outdated --json` parse is guarded against non-JSON output; targeted bump no longer moves a `peerDependencies` entry into `dependencies`; override audit respects pre-release suffixes; env flags accept `on` consistently; corepack failures are surfaced; the `nodeLinker: pnpm` ghost-bump guard stays active; dead code removed.
- **Python/Go/Rust/Dependabot:** Poetry 2.x detection (`Upgrading` verb + stderr); PEP 503 name normalization (`.`→`-`); pip-tools warns on a swallowed uv failure; uv/cargo workspace-child suffix matching; `go.work` single-line `use` parsing; Rust pre-release version capture; the dependabot resolved-version scan skips `node_modules`/`vendor`/`.git`.

## [0.16.0] - 2026-06-12

### Added

- **`--commit-umbrella` and `--actions-org-policy` flags** wiring the v0.15.0 env-gated features; Actions policy rows now render in the report (`[policy]` lines, per-bundle and footer `actions policy violations` counts, JSON summary field). (#125)
- **Container ecosystem v2 (#126):** generic OCI registry-v2 challenge flow (quay.io, gcr.io, mcr.microsoft.com, and any spec-compliant public registry now resolve — no more per-registry hardcoding); basic-auth from `~/.docker/config.json` (`DOCKER_CONFIG` honored; credential helpers out of scope → report-only); opt-in digest refresh for `tag@sha256` pins via `REPO_OPS_CONTAINER_REFRESH_SHA=1`; compose `build.dockerfile` references resolved and included in discovery.
- **Audit/dependabot hardening (#128):** dependabot parses conventional-commit prefixes, `in /path` variants, grouped PRs (body table via `gh pr view`), and `[Security]` titles; satisfaction checks use real lockfile parsers (npm/yarn/pnpm/bun/go.mod/Cargo.lock) with semver comparison — text-search fallback is flagged low-confidence and never auto-closed; audit gains a documented severity-normalization table per tool, an `unknown` bucket (govulncheck) with `--fail-on-unknown`, and robust pip-audit/bun-audit probes; govulncheck integration test added.

### Changed

- **yarn apply ghost-guard probe reuses the check-phase plan (#127):** per-run cache keyed by workspace; the isolated resolutions-stripped probe runs only when apply executes without a prior check — one fewer `yarn up` + `yarn install` per workspace.

## [0.15.0] - 2026-06-12

### Added

- **`repo-ops audit` subcommand:** fleet vulnerability sweep running the native auditor per ecosystem when present (govulncheck, npm/yarn/pnpm/bun audit, pip-audit, cargo audit), normalized severity counts, `--fail-on {critical,high,moderate,low,never}` (default high), human table + JSON.
- **`repo-ops dependabot` subcommand:** finds open Dependabot PRs (via `gh`) whose bump the repo already satisfies; dry-run report by default, `--close` closes them with a supersession comment.
- **Container image ecosystem:** Dockerfile `FROM` and compose `image:` pins are discovered as `container` workspaces; tags are checked against docker.io/ghcr.io (anonymous v2 API), bumped in-major with suffix awareness (`-alpine`, `-slim`), byte-preserving single-line edits; SHA-pinned and non-semver tags report-only; cross-major behind `REPO_OPS_CONTAINER_UPDATE_LATEST=1`. No build/verify in v1.
- **Targeted behind-latest bumps for npm and pnpm** (yarn parity, #122): per-package bump-to-latest with verify + revert-on-fail, preserving manifest range style; gated by `REPO_OPS_NPM_TARGETED_BUMP` / `REPO_OPS_PNPM_TARGETED_BUMP` and `--npm-targeted-bump` / `--pnpm-targeted-bump`.
- **Go directive/toolchain management:** go.work `go`-directive drift vs member requirements is now a planned Change (apply runs `go work use`); opt-in fleet bumps via `REPO_OPS_GO_DIRECTIVE` (`go mod edit -go` + tidy) and `REPO_OPS_GO_TOOLCHAIN`.
- **Org Actions-policy audit (opt-in, `REPO_OPS_ACTIONS_ORG_POLICY=1`):** every workflow `uses:` is evaluated against the org's allowed-actions policy (all/local_only/selected with pattern matching); violations report as `policy` rows — catches the "org allowlist silently startup-fails all CI" class.
- **Umbrella submodule pointer-bump commits (opt-in, `REPO_OPS_COMMIT_UMBRELLA=1`):** after children-first commits, umbrella repos get a `chore(submodules): bump N child pin(s)` commit staging exactly the gitlinks of children committed this run.
- **Dirty-skip aging:** persistent state (`~/.cache/repo-ops/state.json`) records first-seen date and run count per dirty-skipped repo; skip lines show `(dirty since YYYY-MM-DD, N runs)`; disable with `REPO_OPS_NO_STATE=1`.
- **Per-repo verify profiles:** `verify.script`, `verify.skip`, `verify.timeout` read from `[tool.repo-ops]` in pyproject.toml or a new `.repo-ops.toml` sidecar (for non-Python repos); precedence env > per-repo > global config.
- **actionlint verdict caching (#123):** content-hash cache at `~/.cache/repo-ops/actionlint-cache.json` (90-day/4096-entry prune, atomic writes); disable with `REPO_OPS_NO_LINT_CACHE=1`.
- **yarn ghost-bump integration test (#121)** plus bun/npm/go tier extensions.

### Fixed

- **yarn ghost bumps now detected:** `apply_yarn` resolves intended targets (resolutions-stripped probe) and runs the shared ghost-bump guard after apply — a `resolutions` pin blocking an upgrade now fails the workspace instead of silently reaching commit. Plug'n'Play workspaces honestly skip the guard with a note. (#121)
- **Env-overridable timeouts apply everywhere (#118):** all plugin checkers/appliers (go, rust, uv, hatch, pip-tools, poetry, yarn, bun, npm, pnpm, remediations) migrated from bare `TIMEOUT_CHECK`/`TIMEOUT_APPLY` constants to the `REPO_OPS_TIMEOUT_*` resolvers.

### Changed

- **mypy `disallow_untyped_defs` enabled for all 20 packages** (#120).
- **Dead `_walk_apply` YAML-rewrite path removed** (#119).

## [0.14.0] - 2026-06-12

### Added

- **Ghost-bump guard for npm, pnpm, and bun's fallback path:** the post-apply resolution guard (added to bun's targeted path in #112) is now a shared `repo-ops-js` helper wired into `apply_npm`, `apply_pnpm`, and bun's bare-`bun update` fallback — an `overrides`/`resolutions`/exact-pin that blocks the resolver now fails the workspace with a `ghost bump:` error instead of reporting false-green. Pre-release escape closed too: an installed `4.0.0-rc.1` no longer satisfies a stable `4.0.0` target.
- **Actions lint levels:** `--actions-lint-level {block,warn,off}` (or `REPO_OPS_ACTIONS_LINT_LEVEL`) — `warn` records the actionlint failure but proceeds with pin bumps, so an unrelated shellcheck nit in a `run:` block no longer silently blocks every pin in the workflow.
- **Go environment-failure classification:** toolchain/workspace problems (go.work `go` directive older than a member requirement, missing replace targets, missing go.sum entries, toolchain constraints) are prefixed `[env]` with a one-line remediation hint (e.g. ``run `go work use` in <dir>``) instead of raw `go` stderr. Opt-in `REPO_OPS_GO_WORK_SYNC=1` auto-runs `go work use` and retries once.
- **Stronger verify gates:** Go gains `go build ./...` after `go mod verify`; uv workspaces run `uv run ruff check .` when ruff is configured; Node verify script is overridable via `REPO_OPS_NODE_VERIFY_SCRIPT` (empty value skips); hatch heuristics recognize `check`/`typecheck` scripts and mypy.
- **`--exclude PATTERN`** (repeatable, substring or glob against repo label/path) to suppress known-broken repos without code edits.
- **Layered settings:** new `repo_ops_core.config` module with CLI flag > env var > config file > default precedence; per-repo `[tool.repo-ops]` in `pyproject.toml` and global `~/.config/repo-ops/config.toml`; consolidated settings reference table in `docs/usage.md`.
- **Streaming progress:** one `[done] <label> (<manager>) — N change(s)` stderr line per finished workspace (suppress with `--quiet` or `REPO_OPS_NO_PROGRESS=1`) — no more silent multi-minute runs.
- **JSON report:** top-level `exit_code` and `generated_at`, plus per-workspace `elapsed_s`.
- **Integration test tier:** `pytest -m integration` exercises real bun/npm/go against fixture repos (ghost-bump end-to-end, go.work classification, workflow byte-preservation); advisory CI job.
- **Bundled skill:** `install-skill` now ships `AGENTS.md` alongside `SKILL.md` (offline quick reference), with a byte-identity drift test.
- **PyPI trusted publishing:** `release.yml` gains an OIDC publish job (requires one-time PyPI trusted-publisher + `pypi` environment setup).

### Fixed

- **Actions-only commits no longer sweep pre-existing dirty files:** the post-apply commit stages only paths this run actually modified (workspace lockfiles/manifests from results plus explicitly bumped workflow paths) — a pre-dirty `yarn.lock` stays dirty instead of riding along in a `chore(ci)` commit. Also fixed sibling-directory prefix matching (`packages/foo` vs `packages/foobar`). (#116)
- **Pin bumps are byte-preserving:** workflow pin updates now edit only the `uses:` line in the original text instead of round-tripping the whole YAML document — no more 51-line reindent diffs for a one-line bump. (#117)
- **Mixed-ecosystem commit subjects:** the conventional-commit classifier enumerates all matched ecosystems instead of first-match-wins, so a Go+Node+CI commit is no longer titled `chore(ci)`.
- **pnpm latest-mode omission:** a package with unknown registry `latest` but a known in-range `wanted` bump is no longer silently dropped from the plan.
- **Biome remediation in wildcard monorepos:** `workspaces: ["packages/*"]` patterns are now glob-expanded, so `biome migrate` runs after `@biomejs/biome` upgrades in standard monorepos; playwright detection includes `peerDependencies`/`optionalDependencies`; snapshot diffs include `optionalDependencies`.

### Changed

- **Performance:** discovery `rev-parse` fan-out, the post-pool Actions scan, the post-apply commit phase, and children-first submodule ordering (topological levels) all run through bounded thread pools; `--jobs` defaults to `min(16, cpu_count)` instead of a hardcoded 8.
- **Timeouts env-overridable:** `REPO_OPS_TIMEOUT_CHECK/APPLY/VERIFY`.
- **CI:** Python 3.13 added to the matrix (3.14 advisory); coverage floor raised 15 → 63 (measured 71%); `disallow_untyped_defs` enabled for `repo_ops_models`.

## [0.13.0] - 2026-06-11

### Added

- **Actions: exact tag pins advance within their major when upstream has no floating `vN` tag:** pins like `astral-sh/setup-uv@v8.1.0` (only `vN.M.P` tags published) could never move — normalize had no bare major to collapse to and the pin was hidden from the report (`pin == latest`). Such pins now bump to the highest exact tag within their current major, the tag-pin analog of #109's SHA digest refresh. (#115)

### Fixed

- **yarn conservative mode actually upgrades now:** default (non-latest) mode ran bare `yarn up`, a Yarn 4 no-op that resolves and installs but upgrades nothing — every run reported `0 upgrade(s)` on yarn workspaces while plain in-range bumps sat unapplied and Dependabot kept filing PRs repo-ops should have superseded. Conservative mode now runs `yarn up -R '*'` (re-resolve within existing ranges, ranges untouched); latest mode keeps behind-latest reporting and targeted bumps on top. (#114)
- **Go ghost upgrades eliminated:** `go list -m -u all` enumerates the full module graph including modules no build package needs; bumping those was immediately undone by `go mod tidy`, yet they were counted as upgrades — a run claimed "50 package(s) upgraded" with every worktree byte-identical afterward. Check/plan and explicit-mode `go get` targets are now filtered to modules recorded in the workspace `go.mod`. The post-apply fallback that re-ran the checker on the clean tree and reported still-available updates as applied changes is also removed. (#113)
- **Report-only Actions rows no longer print `[ok]`:** rows `apply` never rewrites (newer major in normalize mode, SHA-pinned, no floating major upstream) printed `[ok]`, reading as an applied bump while the workflow file was untouched. They now print `[report-only]`. (#113)

### Changed

- CI: bump `astral-sh/setup-uv` from v8.1.0 to v8.2.0.

## [0.12.0] - 2026-06-07

### Added

- **`--yarn-targeted-bump`: move the `behind_latest` gap, not just report it (option 2 of #110):** with `--latest`, for each direct dependency that `yarn up -R '*'` left behind (peer/group constraints, surfaced as `behind_latest` in #110), repo-ops can now attempt a reversible targeted `yarn up <pkg>@latest`. It snapshots the lockfile/manifests, runs the targeted up plus `yarn install`, and **keeps the result only if install succeeds and the resolved version actually advances** — otherwise it restores the snapshot and the dependency stays `behind_latest`, so nothing broken is committed. The version-advance gate also drops the ghost-bump case where the lock churns but the resolver holds the old version. Opt-in via the flag or `REPO_OPS_YARN_TARGETED_BUMP`; successful bumps appear as normal planned changes, so `plan` previews exactly what `apply` lands. (#111)

## [0.11.0] - 2026-06-05

### Added

- **yarn latest-mode surfaces packages left behind:** after `yarn up -R '*'` (`--latest`), repo-ops now diffs each direct dependency's resolved version against the latest published version and reports any that yarn's resolver kept behind (peer/group constraints) as `behind_latest`, instead of silently omitting them. A supersede run no longer reads as "everything is current" when a newer version exists but wasn't applied (e.g. `@sentry/nextjs` `10.55.0` while `10.56.0` is published). Only runs in latest mode; a registry lookup failure skips that package rather than failing the run. (#110)
- **`--actions-refresh-sha` flag:** keeps a SHA-pinned action pinned **by digest** but advances the SHA to the commit of the newest `vN.M.P` release **within its current major**, preserving the `# vN` audit comment. Lets repo-ops supersede Dependabot's digest-pin bumps (e.g. `actions/checkout` `6.0.2 → 6.0.3`) without un-hardening the pin to a floating tag. The major is resolved from the `# vN` comment, else the tags API; no effect with `--actions-unpin-sha`. (#109)
- **Subcommand `--help` shows full flags:** the top-level dispatcher delegates `repo-ops plan --help` / `apply --help` (etc.) to the real subcommand parsers so `--latest`, `root_dir`, exit-code epilogs, and DEFAULT_ROOTS paths appear without reading source.
- **AGENTS.md quick reference:** invocations, DEFAULT_ROOTS, `--latest` umbrella, workflow, flags cheat sheet, and explicit do-not-explore guidance for routine maintenance runs.

### Fixed

- **Actions-only repos now commit under `--commit`:** `_bundle_apply_ok` bailed with "no workspaces" before considering action bumps, so a repo with workflows but no package manifest applied action bumps to the worktree but never committed them. (#107)
- **A dirty sibling no longer blocks committing a clean workspace's bump:** the post-apply dirty guard was repo-wide (`any(started_dirty)`, itself repo-wide via `is_git_dirty`), so under `--jobs` one dirty/skipped workspace dropped every other workspace's commit. A dirty-start repo now scopes the commit to the upgrade's own paths instead of skipping wholesale; a root-workspace manifest (e.g. `package.json`) is no longer dropped from that scoped commit. (#108)

### Changed

- **`plan`/`apply` `--help` text:** richer `--latest`, `--allow-dirty`, `--jobs`, and DEFAULT_ROOTS help; plan/apply epilogs document exit codes and agent vs human docs.
- **Bundled agent skill:** points to AGENTS.md instead of duplicating flag tables; instructs agents to run named commands directly.

### Documentation

- **Doc tiers:** README/HUMANS/CONTRIBUTING/usage aligned on agent quick reference vs `--help` as canonical flags; SHA-pin default (preserve digest) clarified in usage.md.

## [0.10.0] - 2026-06-05

### Added

- **`--actions-upgrade-mode {normalize,latest}` flag:** controls how the GitHub Actions upgrader treats a pin whose major is behind the newest upstream major line, mirroring `--go-upgrade-mode`. `normalize` (default) keeps the prior behavior — collapse exact tag pins to the floating major and report newer majors as report-only. `latest` **applies** cross-major bumps to the newest available major, honoring `--actions-tag-style` (`major` → floating `@vN`; `exact` → highest `@vN.M.P`) and the existing bare-major safety check; it falls back to normalize/report-only when the newest major cannot be safely targeted. (#106)

### Changed

- **`--latest` now also applies cross-major Action bumps:** the `--latest` umbrella opts GitHub Actions into `latest` mode (equivalent to `--actions-upgrade-mode=latest`), closing the gap where node ecosystems honored `--latest` but Actions cross-major bumps were always report-only. An explicit `--actions-upgrade-mode` overrides the umbrella. Without either opt-in, cross-major Action bumps remain report-only. (#106)
- **Behind-latest footer wording:** the Actions "newer major available" footer hint now points operators at the apply path (`--actions-upgrade-mode=latest` / `--latest`); applied cross-major bumps are counted under "actions bumps" while unresolvable ones remain under "behind-latest". (#106)

## [0.9.0] - 2026-06-04

### Added

- **`--latest` flag:** a single `--latest` flag on `plan`/`apply` enables aggressive (include-majors) upgrades across every ecosystem, replacing the four undiscoverable per-ecosystem env vars (`REPO_OPS_BUN_UPDATE_LATEST`, `REPO_OPS_PNPM_UPDATE_LATEST`, `REPO_OPS_YARN_UPDATE_LATEST`, `REPO_OPS_GO_UPGRADE_MODE`); those env vars are still honored. (#102)
- **`--go-upgrade-mode {patch,explicit,mvs}` flag:** the Go upgrade mode — previously only the `REPO_OPS_GO_UPGRADE_MODE` env var, documented solely in HUMANS.md — is now a first-class CLI flag surfaced in `--help`. (#102)
- **`--json-out PATH` for `pushed` and `inventory`:** both commands can now write their JSON report atomically to a file (via a new `write_json_atomic` core helper) instead of only streaming to stdout, matching `plan`/`apply`. (#104)
- **`--top N` for `inventory`:** caps the displayed rows — the TOTAL is still computed over the full set and "… and N more" is appended; `--json` still emits every row. (#104)
- **`--jobs N` for `pushed` and `inventory`:** the worker-pool size is now configurable (previously hardcoded to `min(16, cpu)` and `8` respectively), matching `plan`/`apply`. (#104)
- **`--manager` filter:** scope a run to a single package manager (bun/npm/pnpm/yarn/uv/poetry/pip-tools/hatch/go/rust), complementing the ecosystem `--filter` for mixed monorepos. (#104)
- **`--version` flag:** `repo-ops --version` now prints the installed version instead of erroring on the required subcommand. (#104)
- **`--only-outdated` for `apply`:** the triage flag (previously plan-only) now also works on `apply`. (#103)
- **`--compact` flag:** emits single-line (unindented) JSON across all three producers for NDJSON-adjacent piping. (#101)
- **`schema_version: 1`** is now the first key in every JSON payload (plan/apply, pushed, inventory), giving consumers a migration gate. (#101)
- **"packages upgraded" footer total:** the run footer (and JSON `summary`) now reports the total package-upgrade count across workspaces. (#103)

### Changed

- **Per-ecosystem behind-latest hints:** the human-report footer now gives each manager its correct "include latest" remediation (bun/pnpm/yarn → `--latest`; npm → "no latest mode; bump the pin manually") instead of the one-size-fits-all `REPO_OPS_BUN_UPDATE_LATEST=1` advice, which was wrong for npm/pnpm. (#102)
- **Triage-sorted report under `--only-outdated`:** repos now sort heaviest-first (by upgrade count) when `--only-outdated` is set; full reports stay alphabetical. (#103)
- **Aligned change rows** (`name  from → to` via `table.py`) and **suppressed empty `## repo` headers** under `--only-outdated --skip-actions` in the human report. (#103)
- **JSON payloads omit null fields:** `error`, `verify_error`, and `detail` are now omitted when null (matching the existing omit-when-None fields), shrinking large payloads. (#101)
- **Harmonized JSON roots key:** `inventory` now emits `roots` (was `scanned_roots`), consistent with plan/apply and pushed. (#101)

### Fixed

- **`set_version.py` show mode** now exits 1 when any manifest version is unparseable, completing the exit-code contract #98 added to the set/bump paths (the show path silently exited 0). (#100)

### Documentation

- Documented the `REPO_OPS_BUN_UPDATE_LATEST` and `REPO_OPS_FIND_TIMEOUT` environment variables, and removed the pre-1.0 0.2.x `repos-*` migration guide (no migration guide needed for a pre-1.0 project).
- Refocused `HUMANS.md` and `AGENTS.md` as concise entry docs and moved the deep reference (installation, usage, bulk maintenance, programmatic imports, testing, architecture) into a new `docs/` canon directory linked from each.

## [0.8.0] - 2026-06-04

### Added

- **CI dependency audit:** a dedicated `audit` job runs `pip-audit` on every push/PR, scanning the resolved third-party dependency tree for known CVEs (`--skip-editable` drops the local workspace packages).

### Changed

- **Expanded lint coverage:** ruff now enforces flake8-bandit (`S`), flake8-simplify (`SIM`), flake8-comprehensions (`C4`), and ruff-native (`RUF`) rules on top of `E/F/I/UP/B`. `S603`/`S607` are ignored project-wide (invoking package managers by name off `PATH` is repo-ops's purpose, not a vulnerability) and tests ignore `S101`/`S108`; the remaining best-effort exception swallows are annotated. The `scripts/` directory is now linted and formatted alongside the packages.

### Fixed

- **Actions pin update (read-only / vanished workflow file):** `update_workflow_pins` now wraps its YAML load *and* write in `OSError` handling, raising `WorkflowParseError` like `parse_workflow_uses` already did, so a read-only or mid-scan-deleted `.github/workflows/*.yml` is recorded as a failed bump instead of an uncaught `PermissionError`/`OSError` aborting the entire actions run. (#77)
- **Override audit (range/caret pins):** `overrides_audit` no longer fires a false `OVERRIDE stale` for range pins (`^1.2.3`, `~`, `>=`). The string-equality stale check is now gated to exact semver pins, so an in-range higher resolved version (e.g. `1.2.5` satisfying `^1.2.3`) passes silently while the floor comparison still flags genuine downgrades. (#78)
- **Lockfile version parsers (bogus tokens):** the pnpm packages-section parser strips the parenthesized peer-dep suffix (`@babel/core@7.21.0(supports-color@8.1.1)` → `@babel/core@7.21.0`) and the yarn-Berry parser skips the `__metadata` preamble block, so the inventory `versions` set no longer carries garbled pnpm-v6 tokens or a bogus `__metadata@6` token per Berry lockfile. (#79)
- **Commit classifier (`bun.lock`):** `classify_commit_subject` now recognizes Bun ≥1.0's text `bun.lock` (not just the legacy binary `bun.lockb`) as a Node lockfile, so a lockfile-only Bun update gets the "update Node lockfiles and manifests" subject instead of the generic dependency-update fallback. (#80)
- **Override resolution (bare-scope key):** `resolved_version_from_node_modules` no longer crashes with `ValueError` on a slashless scoped override key (e.g. `@types`); the scoped-split branch now requires a `/`, so a single malformed override key degrades to a warning instead of failing the entire workspace check. (#81)
- **Post-apply package.json diff (non-dependency fields):** `collect_changes_after_apply` now tracks which JSON section each diff line is in and only emits changes from dependency sections, so a bumped package `"version"`/`"name"`/`"license"`/script line is no longer captured as a spurious `Change` row polluting remediation context and the commit body. (#82)
- **Post-apply lightweight path (root workspaces):** the fast `git diff --name-only` path now skips its workspace-prefix filter when the workspace is the git root (`rel_s == "."`), so root-workspace applies use the lightweight path as intended instead of always falling back to a full checker re-run. (#83)
- **Commit-agent draft failures (transport errors):** `plan_repo_commit` now catches any `Exception` (not only `RuntimeError`) from the optional cursor commit-agent draft, so a network/transport error (`ConnectionError`/`TimeoutError`) during `--commit=agent` degrades to a skipped commit with a reason instead of crashing the commit phase after a successful apply. (#84)
- **HUMANS.md (plan-phase Corepack):** corrected the stale "Node check does not run Corepack" note — the yarn snapshot check runs `corepack prepare` during plan, so plan has a corepack side-effect and may access the network. (#85)
- **Reconciliation dict (loop-invariant):** `run.py` builds the `path_to_result` map once before the bundle loop instead of rebuilding it per bundle, removing the O(bundles × results) rework on large multi-repo scans (output unchanged). (#86)
- **requirements.txt parser (extras & inline markers):** `parse_requirements_lines` now matches an optional `[extras]` group (keyed by the bare package name) so `requests[security]==2.31.0` is no longer dropped from the change diff, and stops the version capture at whitespace or `;` so an inline marker (`flask==3.0.0;sys_platform=="win32"`) no longer pollutes the captured version. (#87)
- **`--top` validation (negative counts):** `--top` now uses the `positive_int` validator (matching `--jobs`), rejecting negative values that previously corrupted the per-workspace change slice and inflated the "… and N more" count; the same unguarded `--top` in `repo-ops-pushed audit` is fixed too. (#88)
- **Post-apply fallback (dropped lockfiles):** `collect_changes_after_apply` now derives lockfile names from the manager constants instead of an `.endswith(".lock")` heuristic, so the fallback path no longer drops `package-lock.json`, `pnpm-lock.yaml`, and `bun.lockb` (`.json`/`.yaml`/`.lockb` suffixes) from its change list — matching the primary snapshot path. (#89)
- **bun checker timeout:** `check_bun` now passes `timeout=TIMEOUT_CHECK` (600 s) to both `bun outdated` calls instead of the 300 s default — the bun sibling of #57/#64/#70. (#90)
- **pnpm `--latest` plan/apply parity:** `check_pnpm` now honors `REPO_OPS_PNPM_UPDATE_LATEST` (same truthiness as bun), so packages that `apply_pnpm --latest` will upgrade are reported in `changes` instead of the non-actionable `behind_latest` bucket. (#91)
- **Pushed counts (intervening fetch):** `pushed` now resolves each push entry's base via `_reflog_pre_sha` (full-reflog walk) rather than the previous push's tip, so a `git fetch` between two pushes no longer attributes the fetched colleague commits to the later push. (#92)
- **hatch venv checker timeout:** the no-`uv.lock` venv branch of `check_hatch` now passes `timeout=TIMEOUT_CHECK` (600 s) to its `uv pip install -e` call instead of a hardcoded 300 s — completing the #70 fix for the venv path. (#93)
- **Discovery (unreadable subdir):** `discover_repos` now processes `find`'s stdout even on a non-zero exit (one permission-denied subdir no longer silently drops every repo under that root) and warns to stderr — mirroring the timeout branch. (#94)
- **Discovery (vanished repo dir):** the `git_common_dir` call site now also catches `FileNotFoundError`/`OSError` (warn + skip), so a repo directory that disappears mid-scan no longer aborts the whole discovery — the sibling of #71. (#95)
- **Inventory edge count (vendored deps):** `count_dep_edges` now prunes with the shared `SKIP_DIRS` constant instead of an incomplete hardcoded set, so vendored manifests under `vendor/`, `third_party/`, `.yarn/`, etc. no longer inflate the `edges` column out of sync with the other inventory columns — completing #75. (#96)
- **Import-boundary check (dotless imports):** `check_import_boundaries` now applies the submodule allowlist to the dotless `from repo_ops_upgrade import X` form (collecting `node.names`), not just the dotted form, so a disallowed cross-package import is actually flagged. (#97)
- **`set_version` skipped manifests:** `set_version.py` now warns to stderr, counts the skip, and exits non-zero when a manifest's version cannot be parsed (single-quoted, PEP 440 pre/post-release) instead of silently dropping it and reporting success — surfacing the mixed-version drift it exists to prevent. (#98)
- **pip-tools verify gate:** `verify_workspace` now has a real `pip-tools` branch (recompile-and-compare against `requirements.in`) instead of falling through to `return None`, so `--verify` no longer reports a silent false pass for every pip-tools workspace. (#99)

## [0.7.1] - 2026-06-04

### Fixed

- **Workspace-member matching (sibling bleed):** uv/yarn/npm and Cargo workspace-member matching is now separator-bound, so a member pattern targeting `packages/foo` no longer also claims the sibling `packages/foobar`, and a bare `apps` pattern no longer claims `apps-legacy`. Previously a `startswith` prefix test with no path-boundary check silently folded sibling workspaces into a member and dropped them from the outdated scan. Fixed in place in all three checkers (`path_is_uv_workspace_child`, `path_is_yarn_workspace_child`, the upgrade `discovery` matcher). (#59)
- **Version normalization (operator whitespace + build metadata):** `normalize_version` now strips whitespace left behind by a range operator (`>= 1.2.3` → `1.2.3`, previously yielding a `(0, 2, 3)` tuple) and drops `+build` metadata (`1.2.3+build.1` → `1.2.3`, previously `(1, 2, 3, 1)`). Leading `v`/`V` and `-prerelease` stripping are unchanged. (#60)
- **Actions SHA-unpin (short pins):** `--actions-unpin-sha` now resolves short (≤7-char) SHA pins back to a tag by a unique-prefix scan of the full-SHA → tags map. The previous exact-key lookup only matched full 40-char SHAs, so short-SHA pins never resolved. Ambiguous prefixes are left unresolved. (#61)
- **Worker exceptions no longer escape as a clean exit 0:** An exception raised inside a per-workspace upgrade worker (`run_remediations` / `verify_workspace`) is now recorded as a `status: fail` workspace result with the error text, yielding a non-zero exit. Previously the bounded thread pool swallowed the exception and the run reconciliation re-emitted the workspace as its original (clean) result, so a crashed workspace was reported clean with exit 0. (#62)
- **Post-apply diff scan (phantom downgrades):** The `package.json` post-apply diff scan now matches added (`+`) lines only, instead of both `+` and `-`. Capturing removed lines recorded the old version as a spurious downgrade change alongside the real upgrade. (#63)
- **Checker timeouts (uv, Rust):** `check_uv` and `check_rust` now pass `TIMEOUT_CHECK` (600 s) to their dry-run subprocesses instead of falling back to the 300 s default — the sibling fix to #57 for npm/go. (#64)
- **Actions bump counter (inflated count):** `actions_summary()`'s `bumps` counter now dedupes by `(action, pin)`, matching the deduped rows the report renders. Previously the same action+pin appearing in multiple workflow files inflated the summary count above the number of displayed rows. (#65)
- **Hatch uv-guard (uv.lock paths):** The `uv`-on-PATH guard added in #33 now also protects the `uv.lock` check/apply paths (`_check_uv_lock` / `_apply_uv_lock`), so a hatch project with a `uv.lock` but no `uv` on PATH degrades gracefully (warn + no-op) instead of crashing. (#66)
- **bun checker (real `bun` dependency):** A dependency literally named `bun` is no longer rejected by name and is now detected as outdated and upgraded. The `bun outdated` banner that motivated the original exclusion is filtered by structure (it is not a `│`-delimited table row), so the false-negative is fixed without reintroducing the banner false-positive. (#67)
- **Verbose remediation diagnostics (JS):** `repo-ops-upgrade-remediations-js` imported `VERBOSE` by value at import time, so the per-remediation `detect=` diagnostic stayed dead under `--verbose` once `set_verbose()` rebound the module global. It now reads `process.VERBOSE` live. (#68)
- **Inventory on-disk count (nested duplicates):** `count_installed_node` no longer prunes `node_modules` during its walk, so nested `node_modules/**/node_modules` duplicates from version conflicts are counted as the on-disk column's docstring promises instead of being silently excluded. The shared `SKIP_DIRS` constant is untouched (a local skip set drops only `node_modules` for this walk). (#69)
- **Checker timeouts (hatch, pnpm, poetry):** `_check_uv_lock` (hatch), `check_pnpm`, and `check_poetry` now pass `TIMEOUT_CHECK` (600 s) to their dry-run subprocesses instead of the 300 s default, matching their apply paths — the sibling fix to #57/#64 for the three remaining managers. (#70)
- **Discovery (`git_common_dir` timeout):** A `git rev-parse --git-common-dir` that exceeds its 10 s timeout in `discover_repos` is now caught (warn + skip that repo) instead of an uncaught `TimeoutExpired` aborting the entire scan — the sibling fix to #41. (#71)
- **Post-apply commit (quoted paths & renames):** `_parse_porcelain` now unquotes git's C-style-quoted porcelain paths (spaces / non-ASCII, decoded as UTF-8) and extracts the new path from `R old -> new` rename entries, so `git add` no longer fails on a literal-quoted pathspec under a directory containing a space. (#72)
- **pip-compile fallback (`--upgrade`):** The pip-compile dry-run check (used when `uv` is absent) now passes `--upgrade`, so `plan` reports the same upgrades `apply` performs instead of preserving current pins and under-reporting zero changes — the pip-compile sibling of #54. (#73)
- **Actions behind-latest counter (inflated count):** `actions_summary()`'s `behind_latest` counter now dedupes by `(action, pin)` on the same `seen` set as the `bumps` counter, so the "newer major available" footer no longer over-reports when one action+pin is pinned across multiple workflow files — the sibling of #65. (#74)
- **Inventory dep-edge count (`node_modules`):** `count_dep_edges` now prunes `node_modules` from its walk, so the `edges` column counts only declared dependencies (parity with the Python edge counters) instead of ballooning to the sum of every installed package's declared deps. (#75)
- **npm/pnpm behind-latest bucketing:** `check_npm`/`check_pnpm` now bucket out-of-range upgrades (newest `latest` outside the declared semver range) into `behind_latest` and return the `(changes, behind_latest)` tuple, so `plan` and the bump counter reflect what `npm/pnpm update` actually applies instead of listing unreachable `latest` bumps as actionable — the npm/pnpm completion of #22. (#76)

## [0.7.0] - 2026-06-04

### Added

- **Report — behind-latest disposition:** Workspaces with packages that have a newer version available but are not actioned (because latest-mode is off) are now tracked on a distinct `behind_latest` field of `WorkspaceResult` rather than being silently dropped into `status: ok`. Affected managers: **bun** (detects packages where `latest` column > in-range `update` column), and **GitHub Actions** (actions where a newer major is available but cross-major upgrades are intentionally not auto-applied). A summary counter (`packages_behind_latest`, `actions_behind_latest`) and a human footer hint are emitted when any behind-latest items are found. Exit code and `failure_count` are unchanged — behind-latest is report-only. The `WorkspaceResult.to_json()` output gains an additive `behind_latest` array; existing fields are unchanged. Yarn's behind-latest detection is deferred (requires a second install pass) — see #22 for details. (#22)

### Changed

- **GitHub Actions SHA-pin policy (behavior change vs #21):** `scan_actions` now defaults to keeping SHA pins (`keep_sha=True`). Converting a hardened `actions/checkout@<sha>` to a floating major tag now requires explicit opt-in via `--actions-unpin-sha`; the inline version-hint comment (`# v4`) is preserved on SHA-pinned lines. The previous flag `--actions-keep-sha` is retained as a deprecated no-op. This reverses the default established by #21. (#25)

### Fixed

- **Discovery:** Bare default-root `plan`/`apply` now discovers package-manifest workspaces (npm/yarn/pnpm/bun, Go, Python, Rust) at any depth below a root, not just at a fixed `<root>/<org>/<proj>` layout. The non-flat walker previously hardcoded a two-level descent, so checkouts laid out as `/usr/local/src/com.github/<org>/<repo>` (or flat roots like `~/Projects`) silently yielded **zero** workspaces while Actions drift still surfaced — a false sense of completeness. Package discovery now reuses the same depth-agnostic git-repo walk that drives Actions discovery, so both ecosystems cover the same repos. Also surfaces repos nested one level deeper (e.g. `<org>/Bastion/<repo>`) whose dependency drift was previously invisible at every root depth. (#23)
- **Discovery (multiple workspaces per repo):** Each repo is now scanned recursively, so *every* manifest in its tree is enumerated rather than just one per repo. A repo with a Go module at its root and a node app in `web/` now yields both workspaces; repos whose only manifest sits in a subdirectory (`web/`, `www/`, …) are discovered again. uv workspace members (`[tool.uv.workspace].members`, governed by the root `uv.lock`) and npm/yarn `workspaces` members are suppressed so monorepos surface once at the root rather than exploding into per-member entries. (#24)
- **Discovery (no silent coverage gaps):** A manifest that is found but not actionable — `package.json` with no lockfile/`packageManager`, `pyproject.toml` with no recognized manager, `Cargo.toml` with no lock and no `[package]`/`[workspace]`, or any manifest outside a git repo — is now recorded as a **counted** discovery skip with a reason code instead of being dropped silently. The count is collected for human runs too (not only `--json`) and the footer lists each skip with its path and reason, so a coverage gap is no longer indistinguishable from "nothing to upgrade". (#24)
- **Discovery (`requirements.txt` manifest):** Non-flat discovery now includes `requirements.txt` as a trigger file, so a repo whose only manifest was a requirements.txt was no longer silently skipped by the package walker. (#32)
- **Discovery (find-subprocess timeout):** The `discover_repos` find subprocess timeout is now tunable via the `REPO_OPS_FIND_TIMEOUT` environment variable or a `timeout=` parameter; the default remains 60 s. (#34)
- **Discovery (submodule hang):** `apply --submodule-order=children-first` no longer hangs on a registered-but-uninitialized submodule. The topo-sort now skips self-referential links and enforces an iteration cap so a degenerate graph can't spin forever. (#36)
- **Discovery (vendored `.gitmodules`):** `_find_gitmodules` now prunes `SKIP_DIRS` during its walk, so it no longer descends into `node_modules`, `target`, `.venv`, or similar directories and registers vendored `.gitmodules` files as real submodule roots. (#43)
- **Discovery (duplicate SKIP output):** Skip-only directories no longer double-print a `SKIP` line to stderr in nested-repo overlap scenarios; the `seen_paths` set is now updated unconditionally on first encounter. (#46)
- **Discovery (self-referential submodule guard):** The self-referential submodule guard now also catches a `.gitmodules` entry whose `path` field is `.` (the repo root itself). (#47)
- **Discovery (hoisted workspace member counts):** Hoisted yarn/npm workspace members are no longer mis-counted as discovery skips. Workspace-membership is now checked before manager detection, keeping the `discovery skipped` counter meaningful. (#49)
- **Discovery (Cargo workspace members):** Cargo workspace members that have a `[package]` section but no own lockfile are suppressed instead of each surfacing as a standalone Rust workspace. Membership prefers `cargo metadata --no-deps` (implicit path-deps + `exclude`); manifest `members`/`exclude` globs remain the fallback when Cargo is unavailable. `default-members` is not treated as membership. (#52)
- **pnpm lockfile parser:** pnpm lockfiles at `lockfileVersion ≤ 6` using path-notation are now parsed correctly. Additionally, v5 path-notation lines containing an `@` in the peer-dependency suffix are no longer garbled by the `name@version` regex. (#28, #53)
- **Rust checker (stderr):** `check_rust` now reads cargo's stderr (where `Updating … -> …` lines are written), so Rust workspaces correctly report available updates instead of always appearing clean. (#30)
- **Poetry apply (PATH guard):** `apply_poetry` now enforces the same `poetry`-on-PATH guard that `check_poetry` already had, preventing a silent failure when poetry is absent at apply time. (#33)
- **pnpm checker (JSON errors):** `check_pnpm` now warns with a raw-output snippet on `JSONDecodeError` instead of silently returning an empty update list. (#35)
- **Hatch checker:** `check_hatch` now verifies return codes for `uv venv`, `uv pip install`, and `uv pip list --outdated`, surfacing failures instead of silently returning empty results. It also detects the extras group from `[project.optional-dependencies]` (trying `dev` → `test` → `all` → `lint`) instead of hardcoding `.[dev]`. (#37, #44)
- **pip-tools checker:** `check` now compiles with `--upgrade` so the plan reflects the same resolver behavior as `apply`. Previously, a plan could show "nothing to do" and then apply would change everything. (#54)
- **Yarn in-range bump detection:** In-range `yarn.lock` bumps (e.g. `4.17.20` → `4.17.21` within `^4.17.0`) are now detected by diffing the resolved `version:` lines in the lockfile rather than comparing range descriptors, which never changed. (#56)
- **Checker timeouts:** `check_npm` and the `go list` check calls now use `TIMEOUT_CHECK` (600 s) instead of the default 300 s, matching the timeout used by `apply`. (#57)
- **Overrides audit (protocol pins):** `overrides_audit` no longer emits false-positive "stale" or "blocks upgrade" warnings for protocol-style override pins (`workspace:`, `file:`, `link:`, `portal:`, `git+`, `github:`, `https:`, `npm:`, `catalog:`, `http:`, `patch:`). (#27, #50)
- **Version normalization:** `normalize_version` now strips a leading `v` or `V` so `semver_tuple("v4.1.0")` and `semver_tuple("V4.1.0")` compare correctly against bare version strings. (#29, #58)
- **Bun checker (overrides audit on clean runs):** The bun tuple-checker now always runs `overrides_audit`, even when the new `behind_latest` list is empty. Previously override auditing was skipped on the common clean-run path. (#42)
- **Bun parser (double-count):** The bun text parser no longer double-counts a package into both `changes` and `behind_latest`. (#45)
- **Actions tag cache:** GitHub Actions tag resolution is now cached at the module level across all repos in a multi-repo run. The per-call cache was previously bypassed, causing redundant API fetches and unnecessary rate-limit consumption. (#31)
- **Actions human report:** The human-readable Actions report no longer prints no-op `vX → vX [ok]` rows or duplicate lines (deduped by `(action, pin)` pair). JSON output is unchanged. (#39)
- **Run-summary footer:** The run-summary footer table now includes an `actions failures` row. (#48)
- **`--on-verify-fail=revert`:** The revert path now checks the `git checkout` return code, removes any untracked files that `apply` created (via a `git clean` scoped to the workspace), and records a `reverted_ok` flag so a failed revert can never be reported as `FAILED_CLEAN`. (#26)
- **Push-audit timeout:** A git `TimeoutExpired` in the push-audit reflog and subject lookups is now caught; previously it aborted the entire scan. (#41)
- **Parallel pool error handling:** `run_bounded_pool` now guards `fut.result()` so an exception raised by a worker thread no longer abandons the pool's remaining futures. (#51)
- **`tee_stdout_stderr` cleanup:** `tee_stdout_stderr` now always restores `sys.stdout` and `sys.stderr` in its `finally` block, even if the flush itself raises. Previously a flush failure left the streams pointed at a closed file for the rest of the process. (#55)

### Security

- **`apply` SHA-pin preservation (see Changed):** Hardened `actions/checkout@<sha>` pins are no longer silently converted to floating major tags by default. Unpin behavior requires `--actions-unpin-sha`. (#25)
- **`install-skill --force` source-tree guard:** `install-skill --dest <dir> --force` now refuses when the destination resolves to, contains, or is contained by the source checkout. Previously `--force` could `rmtree` the source tree before the copy began. (#40)

## [0.6.1] - 2026-06-03

### Fixed

- **CLI:** Bare subcommands (e.g. `repo-ops plan` with no path args) no longer fail with `root does not exist: <cwd>/plan`. The dispatcher forwarded `None` instead of the empty remainder list, so the handler's argparse fell back to `sys.argv[1:]` and re-consumed the subcommand token as a positional root. Bare subcommands now correctly scan `DEFAULT_ROOTS`.
- **Types:** Resolved all mypy errors across `inventory`, `upgrade`, and `core` (including a real `_has_actionable_repo_changes` return-type bug that returned a list where `bool` was declared). The CI mypy gate is now green.

## [0.6.0] - 2026-06-03

### Changed

- **GitHub Actions pin policy:** Tag-pinned actions (e.g. `v3.19.0`, `v4`) now normalize to the floating bare-major tag for the *same* major (`v3.19.0` → `v3`, `v4` stays `v4`). Cross-major upgrades (e.g. `v4` → `v6`) are reported but never auto-applied. SHA pins remain report-only. This eliminates per-patch churn on apply consumers and prevents silent breaking major bumps. **Behavior change:** `apply` now writes `v3` instead of `v3.19.0`; consumers that expected exact-patch replacement will see floating major tags instead.

## [0.5.0] - 2026-05-31

### Added

- **Discovery:** `--flat` mode and actions-only repositories (workflow pins without package manifests) participate in plan/apply scans.
- **pip-tools apply:** `requirements.txt` updates via `uv pip compile` when a native pip-tools apply path is unavailable.
- **Release CI:** Tag builds run `pytest` before uploading wheel/sdist artifacts.

### Changed

- **pnpm / yarn:** Default to conservative semver updates (aligned with npm); Yarn isolated plan/apply uses `yarn up` unless `REPO_OPS_YARN_UPDATE_LATEST=1`.
- **Post-apply:** Lockfile snapshot diff detects more change kinds; post-apply commits are scoped on dirty worktrees; checker failures are logged instead of swallowed silently.
- **GitHub Actions:** Actionlint noise reduced; `gh` tag lookups batched; apply skipped when the worktree is dirty.
- **Docs:** Ecosystem upgrade modes, discovery flags, and tier docs no longer pin release versions in agent/human tiers.

### Fixed

- **Security / plan isolation:** Allowlist `packageManager` values; skip Corepack during plan check; Hatch plan checks use a temporary venv; `install-skill` rejects unsafe `--dest` paths; discovery validates cwd and scan roots; default `find` omits `-L` follow symlinks.
- **Parallel apply:** Fail-fast batch exit no longer raises `KeyError` when a sibling repo stops early.
- **CLI:** Reject non-positive `--jobs`.
- **Bun:** Pass `--` before package update targets so flags are not swallowed.
- **Go:** Harden explicit `go get` batches.
- **Inventory:** Skip scan when a default root is missing; prune `SKIP_DIRS` inside `node_modules` dependency counts.
- **pushed:** Resolve pre-push OID from reflog via `rev-parse`.
- **Upgrade apply:** Narrow swallowed exceptions; guard submodule walks; submodule and Actions apply behavior on partially dirty trees.

### Docs

- HUMANS: Go/Bun/pnpm/Yarn upgrade modes, `--flat` / actions-only discovery, and conservative Yarn policy.

## [0.4.0] - 2026-05-31

### Added

- **CLI reliability:** `--log-file` for plan/apply transcripts; `--strict-exit` and verify-failure paths return reliable non-zero exit codes for CI and shell `pipefail`.
- **Plan artifacts:** `--json-out` writes the full plan JSON atomically to a file (stdout contract unchanged).
- **Run summary:** End-of-run roll-up footer with per-repo counts and a capped failure list; JSON reports include matching summary fields ([#14](https://github.com/Rethunk-Tech/repo-ops/issues/14)).
- **Apply policy:** `--on-verify-fail` with `revert` (default keep) to roll back apply changes when verification fails.
- **Submodules:** Discovery, `children-first` ordering via `--submodule-order`, and pointer hints in plan JSON ([#13](https://github.com/Rethunk-Tech/repo-ops/issues/13)).
- **Post-apply commits:** `--commit=conventional|agent` with `--commit-dry-run` and `--commit-on-fail`; pluggable commit-agent providers (default Cursor SDK) behind optional `repo-ops-upgrade[commit-agent-cursor]` ([#15](https://github.com/Rethunk-Tech/repo-ops/issues/15)).
- **GitHub Actions:** Workflow `uses:` pins discovered and updated via ruamel.yaml AST (no substring pin corruption); optional `gh api` resolution for latest action tags; plan/apply output reports workflows scanned.

### Changed

- **Breaking (install only):** Optional Cursor commit-agent extra is named `commit-agent-cursor` (`agent` remains an alias in `pyproject.toml`). Install: `uv pip install 'repo-ops-upgrade[commit-agent-cursor]'`.
- Plan/apply JSON may include new summary, submodule, and Actions-scan fields; consumers should tolerate unknown keys.

### Fixed

- **Bun plan:** Prefer JSON `outdated` output and filter table junk from stdout parsing ([#12](https://github.com/Rethunk-Tech/repo-ops/issues/12)).
- **JS remediations apply:** Report apply outcome text instead of repeating plan copy ([#11](https://github.com/Rethunk-Tech/repo-ops/issues/11)).

### Docs

- HUMANS and bundled skill: bulk-apply playbook, post-apply commit flags, MCP vs headless commit paths, Bastion submodule workflow, `--on-verify-fail`, summary footer, and `--json-out` usage.

## [0.3.5] - 2026-05-31

### Fixed

- **Go mvs apply:** use `go get -u -t all` instead of `go get -u ./...` / `go get -t -u ./...` so transitives and indirects update in one step.

### Changed

- **Docs:** tier dedup (HUMANS install/usage/imports; AGENTS layout/process); install via `packages/repo-ops` and git `#subdirectory=packages/repo-ops`.

## [0.3.4] - 2026-05-31

### Fixed

- **Bun apply:** run `bun update <pkg>@<version>` from `check_bun` results instead of `bun update --latest`, which rewrote lockfile roots to the literal `"latest"` specifier (#8).

## [0.3.3] - 2026-05-31

### Fixed

- **Go plan/apply:** parse `go list -m -u -json` stdout with a streaming JSON decoder so pretty-printed multi-line module objects work (previously treated output as JSONL and failed every Go root).

## [0.3.2] - 2026-05-30

### Fixed

- **GitHub Actions apply:** replace workflow pins only on matched `uses:` lines so shorter tags are not applied as substrings of longer pins (#5).
- **Yarn plan:** isolated snapshots copy `.yarn/releases/` (Berry) and `file:../` sibling checkouts; failed `yarn up` / `install` errors include a stderr tail in plan JSON (#6).
- **Go upgrades:** default `patch` mode aligns plan and apply (`go list -m -u -json` with patch filter; `go get -u=patch all` + tidy). Opt into `explicit` or legacy `mvs` via `REPO_OPS_GO_UPGRADE_MODE` (#7).

### Changed

- HUMANS.md documents Go upgrade modes and Yarn isolated-plan copy semantics.

## [0.3.1] - 2026-05-31

### Added

- Bundled agent skill (`repo_ops/skills/repo-ops/SKILL.md`) and `repo-ops install-skill` to copy it into `~/.cursor/skills/repo-ops/` and `~/.claude/skills/repo-ops/` (`--target`, `--force`, `--dry-run`, `--home`, `--dest`).

### Fixed

- `repo-ops plan` / `apply` with `--json`: discovery SKIP/WARN lines no longer print during the scan; they appear in JSON `skipped` / `warnings` arrays so stdout stays a single JSON document.

## [0.3.0] - 2026-05-30

### Added

- **uv workspace** under `packages/*` (20 internal packages): `repo_ops_core`, `repo_ops_models`, `repo_ops_lockfiles`, `repo_ops_js` (+ npm/pnpm/bun/yarn plugins), `repo_ops_go`, `repo_ops_rust`, `repo_ops_py` (+ uv/poetry/pip-tools/hatch plugins), `repo_ops_upgrade`, `repo_ops_upgrade_remediations_js`, `repo_ops_inventory`, `repo_ops_pushed`, meta `repo_ops`.
- Explicit ecosystem registry (`repo_ops_upgrade.registry`) populated by `repo_ops._register.register_all()` at CLI startup.
- Lockfile parser unit tests in `packages/repo-ops-lockfiles/tests/`.

### Changed

- **Breaking:** Monolith `src/repo_ops` removed. Programmatic imports use `repo_ops_*` package modules (see HUMANS package-layout table).
- **Breaking:** Single console script **`repo-ops`** with subcommands `inventory`, `plan`, `apply`, `pushed` (no flat `repos-*` entry points).
- Lockfile parsing and snapshot/diff consolidated in `repo_ops_lockfiles`; post-apply change detection uses injected checker (no eager `CHECKERS` import hub).
- Shared manifest walks: `repo_ops_core.walk` (`iter_scanned_dirs`, `manifest_kinds`).
- Upgrade parallel execution: `repo_ops_core.parallel.run_bounded_pool`.

### Removed

- **Breaking:** `repos-inventory`, `repos-upgrade-plan`, `repos-upgrade-apply`, `repos-pushed` console scripts.
- **Breaking:** `repo_ops.upgrade.ecosystems` package and compatibility re-exports.
- `reject_legacy_upgrade_flags` and legacy dep_refresh flag migration hints.

## [0.2.5] - 2026-05-29

### Fixed

- CLI tables: shared column padding so headers align with data (`repos-inventory`, `repos-pushed`).
- Inventory `--flat .`: use resolved directory name when `Path('.').name` is empty (Python 3.12+).

## [0.2.4] - 2026-05-29

### Fixed

- Discovery: find `.git` directories without pruning them away (fixes `repos-pushed` and shared repo discovery).
- Inventory: count Python dependency edges from `uv.lock` and `pyproject.toml` lockfiles.

### Changed

- Documentation tier dedup: README defers install details; AGENTS and CONTRIBUTING each hold a single source per tier.

### Added

- HUMANS: document editable `uv tool` install and verification steps for contributors.
- Tests covering repo discovery and `repos-pushed` regression for `.git` pruning.

## [0.2.3] - 2026-05-29

### Changed

- Migrated packaging from hatchling to `uv_build`; project is uv-locked (`uv.lock`, `.python-version`).
- Dev dependencies moved to `[dependency-groups] dev` (replacing `[project.optional-dependencies] dev`).
- CI and contributor docs use `uv sync`, `uv run`, and `uv build` instead of pip/venv.

### Added

- Canonical install documented via `uv tool install` (git URL today; PyPI when published).
- HUMANS installation section for runtime (`uv tool`) vs development (`uv sync`).

## [0.2.2] - 2026-05-29

### Changed

- JSON upgrade output moved to `upgrade/report.py` (`build_json_payload`, `print_json_report`).
- Lockfile snapshot/diff helpers extracted to `upgrade/ecosystems/node_lockfiles.py`.

### Added

- HUMANS package-layout table for programmatic imports (0.2.1+).
- Subsystem tests (actions, remediations, orchestration, report, inventory `--help`).
- CLI `--help` smoke tests invoke domain `main()` directly (no PATH dependency).

## [0.2.1] - 2026-05-29

### Changed

- Decomposed `upgrade/engine.py` into focused modules: `constants`, `workspaces`, `actions`, `remediations`, `verify`, `report`, `orchestration`, and `ecosystems/` (`node`, `yarn`, `go`, `rust`, `python`).
- Test modules renamed for discoverability: `test_upgrade`, `test_inventory`, `test_pushed`.

### Removed

- Monolithic `upgrade/engine.py` (logic moved to submodules above; import `repo_ops.upgrade.ecosystems` or specific submodules instead).

## [0.2.0] - 2026-05-29

### Added

- Four intent-based console entry points: `repos-inventory`, `repos-upgrade-plan`, `repos-upgrade-apply`, `repos-pushed`.
- Optional `repo-ops` dispatcher with subcommands `inventory`, `plan`, `apply`, `pushed`.
- Domain package layout: `core/`, `inventory/`, `upgrade/`, `pushed/`.
- PEP 561 `py.typed` marker.

### Changed

- **Breaking:** Removed `repo-dep-count`, `repo-dep-refresh`, and `repo-pushed-commits` script names.
- Split dependency refresh into explicit plan (read-only) and apply commands.
- GitHub Actions pin audit is on by default for upgrade plan/apply; use `--skip-actions` to omit.
- `repos-pushed` uses unified root arguments (`add_roots_argument`).

### Removed

- `-y` / `--yes`, `--also-actions`, and `--only-actions` flags (migration messages point to new commands).

## [0.1.0] - 2026-05-29

### Added

- Initial bootstrap of the `repo-ops` package as a lightweight Python src-layout project using hatchling for packaging.
- Three console entry point CLIs registered in pyproject.toml: `repo-dep-count`, `repo-dep-refresh`, and `repo-pushed-commits`.
- Supporting community and release documentation (README, HUMANS, AGENTS, CONTRIBUTING, SECURITY, LICENSE, and this CHANGELOG).
- GitHub community health files (PR/issue templates, CODEOWNERS, Dependabot, minimal CI for lint/test).
- Agent coordination policies (AGENTS.md) and mandatory MCP-based incremental conventional commits for automation.
- Python >= 3.10 compatibility, Ruff linting/formatting, pytest configuration, and .editorconfig hygiene.

### Changed

- N/A (initial release).

[Unreleased]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.25.3...HEAD
[0.25.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.25.2...v0.25.3
[0.25.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.25.1...v0.25.2
[0.25.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.25.0...v0.25.1
[0.25.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.22.1...v0.23.0
[0.22.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.22.0...v0.22.1
[0.22.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.20.4...v0.21.0
[0.20.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.20.3...v0.20.4
[0.20.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.20.2...v0.20.3
[0.20.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.20.0...v0.20.2
[0.20.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.18.1...v0.19.0
[0.18.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.17.3...v0.18.0
[0.17.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.17.2...v0.17.3
[0.17.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.17.1...v0.17.2
[0.17.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.5...v0.4.0
[0.3.5]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.5...v0.3.0
[0.2.5]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rethunk-Tech/repo-ops/compare/0000000...v0.1.0
