# Using repo-ops

Entry doc for operators: quickstart, requirements, and links to the full reference.

The **installable package** is `packages/repo-ops`. The repository root is a uv workspace manifest only (`package = false`); `uv tool install` against the clone root fails — use `packages/repo-ops` or the git URL with `#subdirectory=packages/repo-ops` below.

## Quickstart

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git#subdirectory=packages/repo-ops"
repo-ops inventory --help
```

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python >= 3.10
- Git on `PATH`
- Optional: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, …) and `gh` / `actionlint` for Actions pin audits

## Documentation

| Document | Purpose |
|----------|---------|
| [Installation](docs/installation.md) | Runtime install, development setup, agent skill, verify install, environment |
| [Usage](docs/usage.md) | Subcommands, JSON output, plan vs apply, security/discovery, ecosystem upgrade modes |
| [Bulk maintenance](docs/bulk-maintenance.md) | Submodule umbrellas and multi-repo apply playbook |
| [Programmatic imports](docs/programmatic-imports.md) | Python module paths for programmatic use |
| [Testing](docs/testing.md) | Verification and CI commands |
| [Agents guide](AGENTS.md) | AI/agent layout, coordination, and invariants |
| [Contributing](CONTRIBUTING.md) | Commit conventions, code style, git for agents |
