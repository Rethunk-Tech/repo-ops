# Using repo-ops

Authoritative install, environment, usage, programmatic imports, and verification for the `repo-ops` CLI.

The **installable package** is `packages/repo-ops`. The repository root is a uv workspace manifest only (`package = false`); `uv tool install` against the clone root fails — use `packages/repo-ops` or the git URL with `#subdirectory=packages/repo-ops` below.

## Installation

Requires [uv](https://docs.astral.sh/uv/) and Python >= 3.10.

### Runtime

Install into an isolated tool environment (no clone):

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git#subdirectory=packages/repo-ops"
```

Put the tool bin directory on `PATH` (`uv tool dir --bin`; see uv shell hooks).

After PyPI publish:

```bash
uv tool install repo-ops
uv tool upgrade repo-ops   # refresh an existing install
```

### Development

```bash
git clone https://github.com/Rethunk-Tech/repo-ops.git
cd repo-ops
uv sync --group dev
```

Run without a global tool install:

```bash
uv run repo-ops inventory --help
```

Use `uv sync --locked` in CI and before pushing.

Editable global CLI from your clone:

```bash
uv tool install -e packages/repo-ops
```

After pulling changes, refresh the tool and bundled agent skill:

```bash
uv sync --locked --group dev
uv tool install -e packages/repo-ops
repo-ops install-skill --force
```

### Agent skill

After the `repo-ops` tool is on `PATH`:

```bash
repo-ops install-skill
```

| Target | Path |
|--------|------|
| Cursor (`--target both` default) | `~/.cursor/skills/repo-ops/` |
| Claude Code | `~/.claude/skills/repo-ops/` |

Options: `--target cursor|claude|both`, `--force`, `--dry-run`. Do not install under `~/.cursor/skills-cursor/` (Cursor built-ins).

### Verify install

```bash
uv tool list          # expect repo-ops 0.3.5+
uv tool dir --bin
which repo-ops
```

Optional smoke checks (replace the path):

```bash
repo-ops inventory --flat /path/to/checkout
repo-ops plan --skip-actions /path/to/checkout
repo-ops pushed --count-only /path/to/checkout
```

## Environment

- Python >= 3.10 (`.python-version` pins 3.12 for local/uv default)
- Git on `PATH`
- Optional for upgrades: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, …) and `gh` / `actionlint` for Actions pin audits

## Usage

Subcommands (entry point in `packages/repo-ops`):

| Subcommand | Purpose |
|------------|---------|
| `inventory` | Read-only dependency footprint |
| `plan` | Outdated deps, Actions pin drift, remediations (no writes) |
| `apply` | Apply updates, Actions bumps, remediations, verify gates |
| `pushed` | Commits pushed to remotes in a time window (reflog) |
| `install-skill` | Copy bundled agent skill into Cursor/Claude skill dirs |

Default scan roots: [`DEFAULT_ROOTS`](packages/repo-ops-core/src/repo_ops_core/constants.py) (`/usr/local/src`, `~/Projects`, etc.). Pass explicit root directories to override. `--flat` skips the org/proj two-level walk on `inventory` and upgrade `plan`/`apply` (not `pushed`).

`--json` on every subcommand. For `plan`/`apply`, stdout is one JSON object unless `--json-out PATH` is set (then stdout prints only that path and the report is written to `PATH` atomically). JSON reports include top-level `summary` counts (mirroring the human footer roll-up). Discovery notes live in `skipped` / `warnings` for JSON modes; human mode prints discovery lines to stderr. `--filter ECO` limits to `node`, `go`, `rust`, or `py`. `--submodules` adds `submodules[]` to plan/apply JSON; `apply --submodule-order=children-first` processes child git repos before parent umbrellas when `.gitmodules` is present.

### Submodule umbrellas (git-push-order)

Umbrella repos (e.g. Bastion with many Go/Node submodules) need **children-first** apply/push: upgrade and push each submodule checkout, then commit the parent’s submodule pointer, then push the parent. `repo-ops` discovers submodule repos via `find .git` like any checkout; `--submodules` reads `.gitmodules` for explicit parent/child links. Aligns with the **git-push-order** skill (dependency-first push, no force unless asked). See [Bulk maintenance](#bulk-maintenance) step 4 for the full playbook.

### Plan vs apply

- **Plan** never mutates; exit code `1` when actionable drift exists (CI-friendly).
- **Apply** performs updates; `--no-verify`, `--skip-remediations`, or `--skip-actions` narrow scope. On verify failure, `--on-verify-fail=keep` (default) leaves the worktree as-is; `--on-verify-fail=revert` restores pre-apply lockfile snapshots and git-tracked paths under the workspace. Human and JSON workspace rows include `verify_fail_disposition` (`FAILED_WITH_CHANGES` vs `FAILED_CLEAN`) when verify fails.
- Actions pins included by default; `--skip-actions` for dependency-only runs.

### Bulk maintenance

Use this flow when running `plan` / `apply` across many checkouts under a parent root (for example `/usr/local/src/com.github`).

1. **Plan first, capture output.** Apply never substitutes for plan; always review drift before writes.

   ```bash
   repo-ops plan /path/to/parent/root --log-file plan.log
   ```

   Or separate stderr: `repo-ops plan /path/to/parent/root 2>plan.stderr | tee plan.log` (requires `set -o pipefail` so a failed `repo-ops` is not masked by `tee` exiting 0).

   Machine-readable plan (discovery notes stay in `skipped` / `warnings`):

   ```bash
   repo-ops plan --json --json-out /path/to/plan.json /path/to/parent/root
   ```

   Or redirect stdout: `repo-ops plan --json /path/to/parent/root > plan.json 2>plan.stderr`

2. **Review dirty skips.** Plan/apply skip repos with uncommitted changes unless you intend otherwise. In plan JSON or human stderr, look for `dirty git worktree`. Per repo: commit, stash, or re-run with `--allow-dirty` when you accept applying on a dirty tree.

3. **Apply with reliable logs.** Prefer built-in logging so the process exit code reflects failures (no shell pipe needed):

   ```bash
   repo-ops apply /path/to/parent/root --log-file apply.log
   ```

   Human and log output end with a **Summary** roll-up (workspace ok/fail/skip, dirty skips, verify failures, actions bumps, discovery skipped), a capped **Failures** list (`--max-failures`, default 20), then `failures:` and `exit_code:` for automation. JSON mode includes the same counts under top-level `summary`. When piping instead, use `set -o pipefail` so a failed `repo-ops` is not masked by `tee` exiting 0:

   ```bash
   set -o pipefail
   repo-ops apply /path/to/parent/root 2>&1 | tee apply.log
   ```

   `--strict-exit` exits `1` when any workspace is `skip` or `fail` (default: `fail` only). Skipped dirty trees do not fail the run unless `--strict-exit` is set.

4. **Submodule umbrellas.** When a parent repo contains submodules (Bastion-style layouts), apply and push **child** checkouts first, then update the **parent** submodule pointer and commit that last. Same dependency-first idea as the [git-push-order](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md#submodule-umbrellas-git-push-order) agent skill (`~/.claude/skills/git-push-order/SKILL.md`): children before parent pointer.

   ```bash
   repo-ops plan --submodules --json --json-out plan.json /path/to/Bastion
   repo-ops apply --submodule-order=children-first --submodules /path/to/Bastion --log-file apply.log
   ```

   Plan/apply JSON with `--submodules` adds top-level `submodules: [{path, git_repo, parent}]`. After apply, human output and JSON may include `submodule_pointer_hints` when a parent still has dirty submodule gitlinks (commit the parent after children are pushed).

   **Bastion-style example** (umbrella with nested Go/Node submodules under one parent root):

   1. `repo-ops plan --submodules --json-out bastion-plan.json /usr/local/src/com.github/Rethunk-Tech/Bastion` — review `submodules[]` and drift per repo.
   2. `repo-ops apply --submodule-order=children-first --submodules /usr/local/src/com.github/Rethunk-Tech/Bastion --log-file bastion-apply.log` — children apply first; read **Submodule pointers** in the footer for parents needing a pointer commit.
   3. Commit and push each **child** repo (MCP `git-push-order` or `batch_commit` with `push: "after"` on children first).
   4. In the **parent**, stage updated submodule paths and commit (`git add path/to/submodule && git commit -m "chore: bump submodule pointers"`).
   5. Push the parent last.

5. **Verify failures are not always dependency regressions.** Apply runs ecosystem verify gates (build/test). Failures may be missing env vars (for example Next.js `build` needing secrets), flaky tests, or local toolchain gaps — not a bad bump. Inspect logs before reverting. Use `--on-verify-fail=revert` on bulk apply when you want failed workspaces rolled back automatically; default `keep` preserves changes for manual triage. JSON/human output sets `verify_fail_disposition` to `FAILED_WITH_CHANGES` or `FAILED_CLEAN`.

6. **Optional post-apply commits.** After a green apply+verify pass, commit dependency changes in one shot per repo:

   ```bash
   repo-ops apply /path/to/root --commit=conventional --log-file apply.log
   repo-ops apply /path/to/root --commit=agent --commit-dry-run
   ```

   | Flag | Meaning |
   |------|---------|
   | `--commit=none` | Default: no git commits (manual or MCP `batch_commit` in the IDE). |
   | `--commit=conventional` | One `chore(deps): …` (or CI-themed) commit per repo via guarded local `git`. |
   | `--commit=agent` | Draft the subject with the [Cursor SDK](https://cursor.com/docs/sdk/python) (`cursor-sdk`), then commit locally. Requires `CURSOR_API_KEY`. |
   | `--commit-dry-run` | Print intended messages and file lists; no `git commit`. |
   | `--commit-on-fail` | Also attempt commits when verify/apply failed (default: skip failed repos). |

   **Guardrails:** skips repos that were dirty at start unless `--allow-dirty`; never stages `.env`, credential-like paths, or key material; fails closed when verify failed (unless `--commit-on-fail`). Install SDK support: `uv pip install 'repo-ops-upgrade[agent]'` or `pip install cursor-sdk` in the environment running `repo-ops`.

   **IDE agents:** prefer MCP **rethunk-git** `batch_commit` with `push: "never"` when committing from Cursor/Claude; use `--commit=conventional` for headless bulk apply after `apply` logs are green.

**See also (tracking issues):** [#11](https://github.com/Rethunk-Tech/repo-ops/issues/11) remediation apply reporting (closed) · [#12](https://github.com/Rethunk-Tech/repo-ops/issues/12) bun outdated JSON (closed) · [#13](https://github.com/Rethunk-Tech/repo-ops/issues/13) submodule umbrella workflow · [#14](https://github.com/Rethunk-Tech/repo-ops/issues/14) end-of-run roll-up (closed) · [#15](https://github.com/Rethunk-Tech/repo-ops/issues/15) optional post-apply commits (closed) · [#16](https://github.com/Rethunk-Tech/repo-ops/issues/16) plan `--json-out` artifact (closed).

Refresh the bundled agent skill after doc pulls: `repo-ops install-skill --force` (see [Agent skill](#agent-skill)).

### Go modules

`REPO_OPS_GO_UPGRADE_MODE` (default `patch`) aligns plan and apply:

| Mode | Plan | Apply |
|------|------|-------|
| `patch` (default) | `go list -m -u -json`, same minor-line patches only | `go get -u=patch all`, `go mod tidy` |
| `explicit` | all `list -u` updates | batched `go get path@version`, tidy |
| `mvs` | legacy `go list -u -m all` text | `go get -u -t all`, tidy |

### Yarn (plan)

Isolated snapshots copy `.yarn/releases/` (Berry) and `file:../…` siblings from `package.json`; skip `node_modules`, `.yarn/cache`, and other heavy dirs. Failed `yarn up` / `install` errors include a stderr tail in plan JSON `error`.

Run `repo-ops <subcommand> --help` for all flags.

### Migration from 0.2.x

| 0.2.x (removed) | Current |
|-----------------|---------|
| `repos-inventory` | `repo-ops inventory` |
| `repos-upgrade-plan` | `repo-ops plan` |
| `repos-upgrade-apply` | `repo-ops apply` |
| `repos-pushed` | `repo-ops pushed` |
| `repo_ops.commands.dispatch` | `repo_ops.cli` |
| `repo_ops.upgrade.ecosystems` | `repo_ops_upgrade.registry` (after `register_all()`) |

Reinstall operators: `uv tool install -e packages/repo-ops` (clone), `uv tool upgrade repo-ops` (PyPI), or the git `#subdirectory=` URL above. Update automation still calling `repos-*` or importing `repo_ops.upgrade.ecosystems`.

## Programmatic imports

| Area | Module |
|------|--------|
| CLI | `repo_ops.cli` |
| Upgrade entrypoints | `repo_ops_upgrade` (`main_plan`, `main_apply`) |
| Models | `repo_ops_models` |
| Discovery | `repo_ops_upgrade.discovery` |
| Ecosystem registry | `repo_ops_upgrade.registry` |
| GitHub Actions pins | `repo_ops_upgrade.actions` |
| Inventory | `repo_ops_inventory.scan` |
| Push audit | `repo_ops_pushed.audit` |
| Lockfiles | `repo_ops_lockfiles` |
| Node helpers | `repo_ops_js` |
| Shared | `repo_ops_core` |

Filesystem layout of `packages/*`: [AGENTS.md — Layout and files](AGENTS.md#layout-and-files).

## Verification and Testing

Full dev verification:

```bash
uv sync --locked --group dev
uv run python scripts/check_import_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/repo-ops-core/src packages/repo-ops-models/src packages/repo-ops-lockfiles/src packages/repo-ops-js/src packages/repo-ops-js-npm/src packages/repo-ops-js-pnpm/src packages/repo-ops-js-bun/src packages/repo-ops-js-yarn/src packages/repo-ops-go/src packages/repo-ops-rust/src packages/repo-ops-py/src packages/repo-ops-py-uv/src packages/repo-ops-py-poetry/src packages/repo-ops-py-pip-tools/src packages/repo-ops-py-hatch/src packages/repo-ops-upgrade/src packages/repo-ops-upgrade-remediations-js/src packages/repo-ops-inventory/src packages/repo-ops-pushed/src packages/repo-ops/src
uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=15
uv build packages/repo-ops
```

Tests: `tests/`; config in root `pyproject.toml`. CI runs the same checks on Python 3.10–3.12; tag `v*` triggers [release.yml](.github/workflows/release.yml). When changing Markdown tiers, run the doc-audit skill on the repo root (e.g. `~/.cursor/skills/doc-audit` or `~/.claude/skills/doc-audit`).

Pull requests welcome via repository issues.
