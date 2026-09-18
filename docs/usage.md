# Usage

What `plan`, `apply` and `dependabot` do, how they are configured, and what they exit with. Flags: `repo-ops <cmd> --help`.

## Roots

With no `root_dir`, every command scans [`DEFAULT_ROOTS`](../src/repo_ops_core/constants.py) for git checkouts. That is a fleet sweep: repos with uncommitted changes are skipped, and a repo whose verify fails is reverted and retried once on its own after the sweep. A named root is a checkout the operator is working in: it is processed even when dirty (only files apply touched are committed), and a failed verify keeps the changes for inspection.

`--exclude PAT` (substring or glob, matched against a repo's label and path) is unioned with the config `exclude` lists, so a flag can never pull a permanently excluded repo back in. A named root is never excluded. Scan roots must be existing directories; symlinks are not followed.

## plan and apply

`plan` reports what `apply` would do and writes nothing, except that Yarn checks run `corepack prepare` in a temporary copy. `apply` makes the changes, runs remediations, verifies each workspace, and commits.

Every run writes `$XDG_STATE_HOME/repo-ops/runs/<utc>-<cmd>/log` and `report.json` (the newest 50 runs are kept) and prints both paths last. The human report lists only repos with something to show; `report.json` has every row plus a `summary` block.

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
| uv, hatch | `uv lock --upgrade`; a hatch project without `uv.lock` gets one |
| poetry, pdm | `update`, then `add pkg@^X` when the range blocks the newest release |
| pipenv | `pipenv update`, then `pipenv install` per gap when a venv exists |
| pip-tools | recompile `requirements.in`, rewriting its constraint when it blocks the newest release |
| deno | `deno outdated --update --latest`, then `deno update <pkg>` per gap |
| pre-commit | hook `rev` to the newest tag; SHA pins advance to the newest release's commit |
| submodules | pointer to the newest semver tag; SHA-only heads advance within their tagged major |
| containers | `FROM` and `image:` tags to the newest; digest pins refresh for the same tag |
| GitHub Actions | newest major; digest pins advance (comment updated), floating tags become SHA pins |

Node monorepos cover every workspace member, each bumped from its own directory. A private member's `peerDependencies` follow the version the same manifest depends on. When `gh` is authenticated, Actions rewrites are checked against the org's allowed-actions policy and violations are reported.

### Remediations

After an update, `apply` runs the fixes the new versions need: Biome schema migration, Playwright browsers, a declared codegen script, the `packageManager` pin and Biome formatting of rewritten manifests for Node; `gofmt` for Go; `ruff check --fix` and `ruff format` for Python; `cargo fix` for Rust; Deno lint fixes. `plan` lists them.

### Verify

Each changed workspace is verified before it is committed:

1. The ecosystem's lockfile check (`npm ci`, `bun install --frozen-lockfile`, `go mod verify`, `uv sync --locked`, …).
2. The project's declared gates via the [`gate`](https://github.com/Rethunk-Tech/rethunk-gate-cli) CLI (Makefile targets, turbo tasks, `.gate.toml`), tests included.
3. Without `gate`, or where it detects nothing, built-in gates: the first of `ci`, `ci:pushgate`, `build`, `lint` in `package.json`; `go build` and `go test`; `cargo check` and `cargo test`; ruff, mypy and pyrefly when configured.

A failed verify on a fleet sweep restores every file the workspace's apply changed (edits that predate the run are kept) and vetoes the repo's commit. `--no-verify` skips verification and therefore commits nothing.

### Commits

Each verified repo gets one conventional commit staging only the files apply touched, never `.env` or key material. `--no-commit` leaves the changes in the tree. An umbrella repo's children are applied first, and the parent then gets one `chore(submodules)` commit for the gitlinks of children committed this run. Nothing is pushed.

## Config

Two TOML files, read only:

- Global: `$XDG_CONFIG_HOME/repo-ops/config.toml` (default `~/.config/repo-ops/config.toml`).
- Per repo: `.repo-ops.toml` at the repo root. A workspace below the root may carry its own, read for `[verify]` only.

Three keys:

```toml
exclude = ["archived-*", "vendor-fork"]   # repos to skip; unioned across files and --exclude

[verify]
script = "build:prod"   # Node script to run instead of gate detection; "" skips the script step
skip = true             # skip verify for this repo or workspace
timeout = 600           # seconds per verify command (default 60)

[[hold]]
package = "typescript"  # name or glob, e.g. "@types/*"
max_version = "6"       # "6" blocks 7+, "5.9" blocks above 5.9, "5.9.2" is exact
until = "2026-12-01"    # optional; an expired hold is ignored
ecosystem = "node"      # optional: ecosystem or manager name
workspace = "packages/legacy"  # optional glob relative to the repo root
reason = "TS7 migration pending"
```

### Holds

A hold caps one package without excluding the repo. Workspace-scoped rules beat repo-wide ones, which beat global ones. Targets above the ceiling are dropped from `plan`; a version already above it is clamped back on `apply`. Held packages show as `held <= <max_version>` in the report and count toward `packages_held`.

Matching is by package name, module path (Go), crate (Rust), distribution (Python), hook repo (pre-commit), action repo (`actions/checkout`), image name without tag, or submodule path. Go holds cap within a major, since majors live in the module path. Rust clamps with `cargo update --precise` and needs an exact version when no release fits a prefix ceiling. pip-tools holds go through a temporary constraints file; `requirements.in` is untouched.

A workspace that depends on `expo` gets holds from the installed `expo/bundledNativeModules.json` with no config: each listed package is held at its bundled spec, `expo` itself at its installed major, and names in `expo.install.exclude` are skipped. The Expo hold wins over a `[[hold]]` for the same package.

## dependabot

Lists open Dependabot PRs in repos with a GitHub remote (via `gh`) and marks each one superseded when the local lockfile or manifest already has that version. `--close` closes superseded PRs with a comment. Only lockfile-parser matches count; text-search matches, unparseable titles, and grouped PRs with any unconfirmed row are reported and never closed.

## Exit codes

| Command | 0 | 1 |
| --- | --- | --- |
| `plan` | nothing to do | drift, remediations or Actions changes exist |
| `apply` | no workspace failed (dirty skips are fine) | any workspace failed |
| `dependabot` | success | `gh` unavailable or another error |
