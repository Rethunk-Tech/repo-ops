# Using repo-ops

Entry doc for operators: quickstart, requirements, and links to the full reference.

The **installable package** is `packages/repo-ops`. The repository root is a uv workspace manifest only (`package = false`); `uv tool install` against the clone root fails — use `packages/repo-ops` or the git URL with `#subdirectory=packages/repo-ops` below.

## Quickstart

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git#subdirectory=packages/repo-ops"
repo-ops doctor
```

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python >= 3.10
- Git on `PATH`
- Optional: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, …) and `gh` / `actionlint` for Actions pin audits — run `repo-ops doctor` to see which are found and whether `gh` is authenticated

## Documentation

| Document | Purpose |
|----------|---------|
| [Installation](docs/installation.md) | Runtime install, development setup, agent skill, verify install, environment |
| [Usage](docs/usage.md) | Subcommands, JSON output, plan vs apply, security/discovery, ecosystem upgrade modes |
| [Architecture](docs/architecture.md) | Package layout and filesystem map for the uv workspace |
| [Bulk maintenance](docs/bulk-maintenance.md) | Submodule umbrellas and multi-repo apply playbook |
| [Programmatic imports](docs/programmatic-imports.md) | Python module paths for programmatic use |
| [Testing](docs/testing.md) | Verification and CI commands |
| [Agents guide](AGENTS.md) | Agent quick reference, coordination, and workflows |
| [Contributing](CONTRIBUTING.md) | Commit conventions, code style, git for agents |
| [Security](SECURITY.md) | Vulnerability and conduct reporting |
| [Code of conduct](CODE_OF_CONDUCT.md) | Community standards and enforcement |
| [Changelog](CHANGELOG.md) | Release history |
| [License](LICENSE) | MIT License |
