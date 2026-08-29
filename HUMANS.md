# repo-ops — operator runbook

Install, usage, verify, and uninstall for the `repo-ops` CLI. Deep reference: [docs/](docs/). Agents: [AGENTS.md](AGENTS.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python >= 3.12
- Git on `PATH`
- Optional: ecosystem CLIs and `gh` / `actionlint` — run `repo-ops doctor`

## Install

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git"
```

Editable from a clone: `uv sync --group dev && uv tool install -e .`. Full steps: [docs/installation.md](docs/installation.md).

## Usage

| Command | Purpose |
| --------- | --------- |
| `repo-ops doctor` | Toolchain and auth preflight |
| `repo-ops inventory` | Dependency footprint across scan roots |
| `repo-ops inventory --status` | Branch, dirty, ahead/behind per repo (`--fetch` to refresh) |
| `repo-ops plan` | Read-only upgrade preview (exit 1 on drift) |
| `repo-ops apply` | Apply updates and verify gates |
| `repo-ops pushed` | Pushed-commit audit |
| `repo-ops audit` | Fleet vulnerability sweep |

`plan`/`apply` run the full sweep by default; `--conservative` stays in-range. Subcommands and flags: [docs/usage.md](docs/usage.md).

## Verify

```bash
repo-ops doctor
repo-ops --version
```

Smoke: `repo-ops inventory --flat /path/to/checkout`. Install checks: [docs/installation.md § Verify install](docs/installation.md#verify-install).

## Uninstall

```bash
uv tool uninstall repo-ops
```

Agent-skill copies under `~/.cursor/skills/` and `~/.claude/skills/` are not removed automatically — see [docs/installation.md § Uninstall](docs/installation.md#uninstall).

## Environment

- `REPO_OPS_FIND_TIMEOUT` — discovery `find` timeout in seconds (default 60)
- `REPO_OPS_COMMIT_AGENT_MODEL` — model for `apply --commit=agent` (default `composer-2.5`)

Full env and optional CLIs: [docs/installation.md § Environment](docs/installation.md#environment).
