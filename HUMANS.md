# Using repo-ops

Entry doc for operators: quickstart, requirements, and links to the full reference.

The repository is a single installable package (`repo-ops`), built from `src/`. Install the clone root or the git URL directly.

## Quickstart

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git"
repo-ops doctor
```

## Common maintenance

For routine maintenance, run `repo-ops plan` and review it before
`repo-ops apply` — full-coverage sweeping (cross-range upgrades, targeted
bumps, Actions pin/digest refresh, container digest refresh) is the default,
no flags required. Pass `--conservative` to stay within existing manifest
constraints instead.

`--verify-test` enables Go and Rust tests during apply verification. Hatch
verification includes mypy and pyrefly when configured; pyrefly project
excludes are passed through as CLI flags. `doctor` checks for `pyrefly`, `ruff`, `hatch`,
and `pre-commit` alongside the other toolchain binaries.

When using `--commit=agent`, set `REPO_OPS_COMMIT_AGENT_MODEL` or the
`commit_agent_model` config key to select the model (default: `composer-2.5`).

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python >= 3.11
- Git on `PATH`
- Optional: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, `hatch`, `pyrefly`, `ruff`, `pre-commit`, …) and `gh` / `actionlint` for Actions pin audits — run `repo-ops doctor` to see which are found and whether `gh` is authenticated

## Documentation

| Document | Purpose |
| ---------- | --------- |
| [Installation](docs/installation.md) | Runtime install, development setup, agent skill, verify install, environment |
| [Usage](docs/usage.md) | Subcommands, JSON output, plan vs apply, security/discovery, ecosystem upgrade modes |
| [Architecture](docs/architecture.md) | Module layout and filesystem map |
| [Bulk maintenance](docs/bulk-maintenance.md) | Submodule umbrellas and multi-repo apply playbook |
| [Programmatic imports](docs/programmatic-imports.md) | Python module paths for programmatic use |
| [Testing](docs/testing.md) | Verification and CI commands |
| [Agents guide](AGENTS.md) | Agent quick reference, coordination, and workflows |
| [Contributing](CONTRIBUTING.md) | Commit conventions, code style, git for agents |
| [Security](SECURITY.md) | Vulnerability and conduct reporting |
| [Code of conduct](CODE_OF_CONDUCT.md) | Community standards and enforcement |
| [Changelog](CHANGELOG.md) | Release history |
| [License](LICENSE) | MIT License |
