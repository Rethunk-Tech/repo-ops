# Usage

What `plan`, `apply` and `dependabot` do, how they are configured, and what they exit with. Flags: `repo-ops <cmd> --help`.

## Roots

With no `root`, every command scans the default roots (the lines of `bosun roots`, else the built-in list in [constants.py](../src/repo_ops_core/constants.py)) for git checkouts (a fleet sweep). A named root is a checkout the operator is working in.

| | Fleet sweep | Named root |
| --- | --- | --- |
| Uncommitted changes, or a rebase, merge, cherry-pick, revert or bisect in progress | skipped, unless the only dirty files are manifest or lockfile writes a killed apply recorded, whose bytes are unchanged: those are upgraded over and committed with the new bumps, and a failed verify puts them back at HEAD | processed when only manifests and lockfiles are dirty; refused (`dirty: <files>; commit or stash first`) when any other tracked file is dirty |
| Files committed | — | only files apply touched; if apply touched a file you had edited, nothing is committed and the report says `left uncommitted: your edits in <files>` |
| Verify fails | reverted and retried once on its own after the sweep | changes kept for inspection |

Each apply holds a lock in the checkout's git dir until it has committed; a repo another apply holds is skipped, and refusing a named root exits 1.

`--exclude PAT` (substring or glob, matched against a repo's label and path) is unioned with the config `exclude` lists, so a flag can never pull a permanently excluded repo back in. A named root is never excluded. Scan roots must be existing directories. A named root is canonicalized (``Path.resolve``), so a symlink root is followed to its target; the inner ``find`` walk does not follow nested directory symlinks.

## plan and apply

`plan` reports what `apply` would do and writes nothing, except that Yarn checks run `corepack prepare` in a temporary copy. `apply` makes the changes, runs remediations, verifies each workspace, and commits.

Shared flags: `root ...`, `--exclude PAT`, `--jobs N` (default `max(4, min(8, cpus/4))`), `-v` / `--verbose`, `--filter ECO` (`node|go|rust|py|deno|dotnet|container|precommit|actions|submodules`), `--manager MANAGER`. `apply` also takes `--no-commit`, `--no-verify` (which implies `--no-commit`), and `--retry-failed [RUN]`.

Every run writes `$XDG_STATE_HOME/repo-ops/runs/<utc>-<cmd>/log` and `report.json` (the newest 50 runs are kept) and prints both paths last. `apply` then prints one line per failed, skipped or held package (first error line only). The human report lists only repos with something to show; `report.json` has every row plus a `summary` block.

`--retry-failed [RUN]` re-applies only the failed and skipped workspace paths, and repos whose commit failed (`commits` in the report), recorded in that run's `report.json` (newest run if omitted). A fleet sweep ends with a table of dirty-skipped repos: path, dirty-file count, and `repo-ops apply <path>` to name the checkout and update it anyway.

### What moves

Every dependency moves to its newest release, across semver ranges and majors. Each package manager first runs its own update, then a targeted bump for anything still behind; a targeted bump is kept only when the workspace still installs and the version actually advances, otherwise the snapshot is restored.

| Ecosystem | Update |
| --- | --- |
| npm | `npm update`, then `npm install <pkg>@latest` per gap |
| pnpm | `pnpm update -r --latest`, including `catalog:` entries |
| yarn (Berry) | `yarn up -R '*'`, then `yarn up <pkg>@latest` per gap, in an isolated snapshot for `plan` |
| bun | `bun update` plus out-of-range packages from `bun outdated`, then `bun add <pkg>@<version>` |
| go | every `go list -m -u` update via batched `go get path@version`, then `go mod tidy` |
| rust | `cargo update`, plus Cargo.toml requirement bumps (including `[workspace.dependencies]`) |
| nuget | literal `PackageReference`/`PackageVersion` pins in project, `.props` and `.targets` files and `global.json` `msbuild-sdks`, checked against nuget.org plus the feeds the projects and `NuGet.config` add; each bump is kept only when `dotnet restore` succeeds. `global.json` `sdk.version` moves to the newest SDK in the same feature band and major unless `rollForward` pins it |
| uv, hatch | `uv lock --upgrade`; a hatch project without `uv.lock` gets one |
| poetry, pdm | `update`, then `add pkg@^X` when the range blocks the newest release |
| pipenv | `pipenv update`, then `pipenv install` per gap when a venv exists |
| pip-tools | recompile `requirements.in`, rewriting its constraint when it blocks the newest release |
| deno | `deno outdated --update --latest`, then `deno update <pkg>` per gap |
| pre-commit | hook `rev` to the newest tag; SHA pins advance to the newest release's commit |
| submodules | pointer to the newest semver tag; SHA-only heads advance within their tagged major |
| containers | `FROM`, `COPY --from=` and `image:` tags to the newest, any `-suffix` parts (`24-bookworm-slim`) included; digest pins refresh for the same tag |
| GitHub Actions | newest major; digest pins advance (comment updated), floating tags become SHA pins. A new `Rethunk-Tech/gh-actions` tag is applied to one repo until that repo's default-branch CI is green on a commit pinning it; later sweeps bump the rest |

Node monorepos cover every workspace member, each bumped from its own directory. A private member's `peerDependencies` follow the version the same manifest depends on. When `gh` is authenticated, Actions rewrites are checked against the org's allowed-actions policy and violations are reported. Actions scanning covers both `.github/workflows/*.yml` and every composite-action `action.yml`/`action.yaml` in the repo; a `uses:` pin under an excluded workspace glob is left untouched.

### Toolchains

Pinned toolchain versions move too, only upward, keeping the pin's precision (`1.27` waits for a new minor; `1.27.2` becomes `1.27.3`). Node is the exception: its pins move to the newest LTS major, so a sweep's report shows a major move once Node promotes one. They are reported like packages, and `plan` lists them.

| Tool | Pin | Moves to |
| --- | --- | --- |
| go | `go` and `toolchain` lines in `go.mod`, and the `go` line of an enclosing `go.work` (kept level with the module it lists) | newest stable on go.dev |
| rust | a version `channel` in `rust-toolchain.toml` or `rust-toolchain` (`stable`/`beta`/`nightly` stay; Cargo `rust-version` is never touched) | newest stable |
| python | a version in `.python-version` (`requires-python` is never touched) | newest stable CPython |
| node | a version in `.nvmrc` / `.node-version` (`lts/*` and aliases stay), or an exact `engines.node` (`>=` floors stay) | newest LTS release |
| pnpm, bun, npm | `packageManager` | registry latest (a remediation; bun also runs `bun upgrade` and repins every nested `package.json` in the repo); Yarn follows the local runtime |
| bun | `bun-version:` in a workflow (a literal version; `latest` and `${{ … }}` are left alone) | registry latest, same version as the pin |
| uv | `uv-version:`, or `version:` on an `astral-sh/setup-uv` step, in a workflow (a literal version) | PyPI latest |
| go, python, node | `go-version:`, `python-version:`, `node-version:` in a workflow input (a literal version; `latest`, `stable`-style aliases and `${{ … }}` are left alone) | newest patch of the pinned minor (go), newest patch of the pinned minor (python), newest LTS (node) |

Hold one with `[[hold]]` named `go`, `rust`, `python`, `node` or `dotnet`. A failed lookup warns and leaves the pin alone.

### Remediations

After an update, `apply` runs the fixes the new versions need: Biome schema migration, Playwright browsers, a declared codegen script, the `packageManager` pin (bun: every `package.json` in the repo that pins `bun@…`, plus `bun upgrade` once per sweep when the local bun trails), literal `bun-version:` workflow inputs, and Biome formatting of rewritten manifests for Node; `gofmt` for Go; `ruff check --fix` and `ruff format` for Python; `cargo fmt` for Rust; `deno fmt` for Deno. `plan` lists them.

### Verify

Each changed workspace is verified before it is committed:

1. The ecosystem's lockfile check (`npm ci`, `bun install --frozen-lockfile`, `go mod verify`, `uv sync --locked`, …).
2. The project's declared gates via the [`gate`](https://github.com/Rethunk-Tech/rethunk-gate-cli) CLI (Makefile targets, turbo tasks, `.gate.toml`), tests included.
3. Without `gate`, or where it detects nothing, verify fails; no script or command is guessed. `verify.script` still names a `package.json` script explicitly.

Two managers skip that pair: a `container` workspace digest-checks only the `FROM` / compose `image:` refs the apply rewrote (HEAD/GET of each rewritten ref's manifest; anonymous token when the registry challenges; no pull and no build). A ref that does not resolve fails verify with the ref and the registry or network error named. A `precommit` workspace runs `pre-commit validate-config` only — not `gate`.

Repo commands (lockfile checks, `gate`, remediations) run with user-global tool config redirected to `<run dir>/env/`, seeded from the operator's files: `GOENV`, `NPM_CONFIG_USERCONFIG`, `GIT_CONFIG_GLOBAL`, plus an empty `CARGO_INSTALL_ROOT`. A build's `go env -w`, `npm config set`, `git config --global` or `cargo install` therefore cannot change the operator's setup; package caches (`GOMODCACHE`, `CARGO_HOME`, npm cache) stay shared. repo-ops' own `git` calls keep the real config for signing and credentials.

| Situation | What happens |
| --- | --- |
| Failed verify on a fleet sweep | Restores only the files that workspace's apply wrote, and only while they still hold what apply wrote. Anything else that changed in the checkout since (another agent's edits, new files) is left alone and named. The failure is `FAILED_WITH_CHANGES`. The repo's commit is vetoed. |
| Dirtiness | Re-checked before each workspace and before the retry. A dirty checkout is skipped. A failed repo whose checkout became dirty is not retried. |
| Dirty only in manifest or lockfile writes a killed apply recorded (`repo-ops-inflight.json`), bytes unchanged | Counts as clean, so a killed run's leftovers cannot freeze a repo's upgrades. Those files are committed with the new bumps. A failed verify reverts them to HEAD and says so. A hand-edited manifest is dirty, and the repo is skipped. |
| Opt-in e2e gates that `gate` lists as skipped | Not run by verify or by `gates`. `gates --e2e` runs them. |
| `--no-verify` | Skips verification and therefore commits nothing. |

## gates

`gates` runs each repo's own gate and reports the failures together. `gates --e2e` includes the e2e gates a default run leaves out. Apply and verify are unchanged. The two flags combine: a fresh non-partial pass skips the run, and a run that happens still follows `--e2e`.

`gates --skip-fresh` asks `gate --json record` for each repo's verdict.

| Record | What runs |
| --- | --- |
| result matches current HEAD and working-tree hash, exited 0, not partial | skipped (`skip_reason: "fresh"`; `gate_record_reason` omitted). Summary and `report.json` count the repo as skipped |
| partial (a named run, not the whole gate), even if it exited 0 | a bare `gate` run; row includes `record_partial: true`; `report.json` summary counts `record_partial` |
| missing `gate`, or unparseable answer | treated as stale; still runs |
| dirty tree, failed, stale, or missing record | still runs |

When `--skip-fresh` is on and the repo still runs, the row includes `gate_record_reason` (`missing`, `stale`, `partial`, or `failed`), the same `reason` `gate --json record` reports and in the same order: a tree or schema mismatch is stale even when the run failed, a non-zero exit is failed even when partial, and partial is what remains. `skip_reason` is omitted unless the row was skipped as fresh. `gate_record_reason` is omitted when `--skip-fresh` is off.

### Commits

Each verified repo gets one signed conventional commit staging only the files apply touched, never `.env` or key material. With no signing key configured, the commit fails rather than land unsigned. `--no-commit` leaves the changes in the tree. An umbrella repo's children are applied first, and the parent then gets one `chore(submodules)` commit for the gitlinks of children committed this run. Nothing is pushed.

## Config

Two TOML files, read only:

- Global: `$XDG_CONFIG_HOME/repo-ops/config.toml` (default `~/.config/repo-ops/config.toml`).
- Per repo: `.repo-ops.toml` at the repo root. `[verify]` merges global, then this file, then a workspace `.repo-ops.toml` below the root (that nested file is read for `[verify]` only).

Seven keys:

```toml
owned_orgs = ["Rethunk-Tech"]   # global file only; required (no built-in list; commands stop with a config error without it); a checkout whose origin org is not listed is skipped
adopted = ["Rethunk-AI/toml-patch"]   # global file only; forks swept like our own repos (see below)
exclude = ["Rethunk-Tech/gh-actions"]   # repos to skip; unioned across files and --exclude
# In a repo's own .repo-ops.toml, an entry also skips the workspace at that
# repo-relative path or glob (e.g. "go/__patterns/*"); the root workspace never matches.

[verify]
script = "build:prod"   # Node: this package.json script instead of gate; "" is lockfile check only
skip = true             # skip verify for this repo or workspace
timeout = 120           # seconds per verify command (default 1800)

[soup]
path = "docs/SOUP.md"   # repo-relative table whose `name` | `version` rows follow each bump

[generate]
run = "bun scripts/gen-credits.ts"  # after a bump, before verify; non-zero is a verify failure; changed files commit with the sweep

[[hold]]
package = "typescript"  # name or glob, e.g. "@types/*"
max_version = "6"       # "6" blocks 7+, "5.9" blocks above 5.9, "5.9.2" is exact
until = "2026-12-01"    # optional; an expired hold is ignored
ecosystem = "node"      # optional: ecosystem or manager name
workspace = "packages/legacy"  # optional glob relative to the repo root
reason = "TS7 migration pending"
```

### Forks and archived repos

A checkout with an `upstream` remote is a fork and is skipped, as is an archived repo and any checkout whose origin org is outside `owned_orgs`; do not list them in `exclude`. The summary shows a `foreign skipped` row naming what the fork and org rules skipped. To sweep a fork anyway, list its `org/repo` in `adopted`.

### Holds

A hold caps one package without excluding the repo. Workspace-scoped rules beat repo-wide ones, which beat global ones. Targets above the ceiling are dropped from `plan`; a version already above it is clamped back on `apply`. Held packages show as `held <= <max_version>` in the report and count toward `packages_held`.

Matching is by package name, module path (Go), crate (Rust), package id (NuGet), distribution (Python), hook repo (pre-commit), action repo (`actions/checkout`), image name without tag (the short name, the canonical `docker.io/library/...` form, and a registry-qualified name all match the same image), or submodule path. Go holds cap within a major, since majors live in the module path. Rust clamps with `cargo update --precise` and needs an exact version when no release fits a prefix ceiling. pip-tools holds go through a temporary constraints file; `requirements.in` is untouched. A ceiling with a prerelease tag (`81.0.5-ngd.0`) is exact: later prereleases and the release itself exceed it.

A workspace that depends on `expo` gets holds from the installed `expo/bundledNativeModules.json` with no config: each listed package is held at its bundled spec, `expo` itself at its installed major, and names in `expo.install.exclude` are skipped. The Expo hold wins over a `[[hold]]` for the same package.

## report

`repo-ops report [RUN]` prints one line per failed, skipped or held package from that run's `report.json`, with only the first error line. RUN is a run directory, a `report.json` path, or a run id; the newest run is used when omitted. Read-only: it does not plan or apply.

## owned

`repo-ops owned PATH... [--json]` applies the rule `plan`, `apply`, `gates` and `dependabot` use to skip checkouts, and prints the verdict per path: `owned`, or skipped as `excluded`, `not our org` or `fork` (an `upstream` remote, or a GitHub origin GitHub says is a fork; `adopted` opts it back in). Read-only. bosun keeps its own copy of the rule for its tick and tests it against this command.

## dependabot

Lists open Dependabot PRs in repos with a GitHub remote (via `gh`) and marks each one superseded when the local lockfile or manifest already has that version. `--close` closes superseded PRs with a comment. Only lockfile-parser matches count; text-search matches, unparseable titles, and grouped PRs with any unconfirmed row are reported and never closed. A PR stays open while any locked copy of the package is still between its from and to versions, and `--close` closes nothing in a checkout that has uncommitted changes or commits not on its remote default branch.

## Exit codes

| Command | 0 | 1 |
| --- | --- | --- |
| `plan` | nothing to do | drift, remediations or Actions changes exist |
| `apply` | no workspace failed (dirty skips are fine) | any workspace failed, or a named root was refused (archived, or held by another apply) |
| `report` | report.json read | no matching run or report.json |
| `dependabot` | success | `gh` unavailable or another error |
| `gates` | no repo failed | a repo's gate failed or errored |
