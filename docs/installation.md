# Installation

Install, environment requirements, the bundled agent skill, and verify-install checks for the `repo-ops` CLI.

Requires [uv](https://docs.astral.sh/uv/) and Python >= 3.10.

## Runtime

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

## Development

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

Refreshing the CLI or its bundled skill does not update copies already installed
under `~/.cursor/skills/` or `~/.claude/skills/`. Run
`repo-ops install-skill --check` to detect drift without writing; use
`repo-ops install-skill --force` to update those copies.

## Agent skill

After the `repo-ops` tool is on `PATH`:

```bash
repo-ops install-skill
```

| Target | Path |
| -------- | ------ |
| Cursor (`--target both` default) | `~/.cursor/skills/repo-ops/` |
| Claude Code | `~/.claude/skills/repo-ops/` |

Options: `--target cursor|claude|both`, `--check`, `--force`, `--dry-run`, `--home DIR` (or `REPO_OPS_HOME`). `--check` is read-only and exits 1 when an installed copy differs from the bundled skill. For testing only, `--dest DIR` must resolve to a directory named `repo-ops` (refuses to overwrite unrelated paths). Do not install under `~/.cursor/skills-cursor/` (Cursor built-ins).

## Verify install

```bash
uv tool list          # expect repo-ops listed
repo-ops --version
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
- `REPO_OPS_FIND_TIMEOUT` — repo-discovery `find` subprocess timeout in seconds (default 60); accepts any positive float
