<h1 align="center">repo-ops</h1>

<div align="center">

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml)

Local repository operations and dependency maintenance tools for Rethunk-Tech

</div>

---

repo-ops is a **uv workspace** of packages for scanning many local git checkouts under shared roots: dependency footprint reporting, read-only upgrade planning, apply-with-verify upgrades (including GitHub Actions pin bumps), and push-reflog audits.

Install the `repo-ops` console script and use subcommands `inventory`, `plan`, `apply`, and `pushed` (see [HUMANS.md](HUMANS.md)). Upgrading from 0.2.x `repos-*` scripts: [HUMANS — Migration from 0.2.x](HUMANS.md#migration-from-02x). Shared helpers live in `repo_ops_core`; domain logic is split across `packages/repo-ops-*`.

## Highlights

- Single `repo-ops` CLI with `inventory`, `plan`, `apply`, and `pushed` subcommands
- Upgrade plan/apply includes GitHub Actions pin drift by default (`--skip-actions` to omit)
- uv-locked src-layout package (`uv_build`); Python >= 3.10
- Subsystem modules for ecosystems, remediations, verify gates, and workspace discovery
- Designed for Rethunk-Tech monorepo and sibling repository workflows

## Documentation

| Document | Purpose |
|----------|---------|
| [Humans guide](HUMANS.md) | Installation, configuration, and usage |
| [Agents guide](AGENTS.md) | AI/agent layout, coordination, and invariants |
| [Contributing](CONTRIBUTING.md) | Contributor guidelines and code style |
| [Security](SECURITY.md) | Vulnerability reporting |
| [Changelog](CHANGELOG.md) | Release history (Keep a Changelog style) |
| [Code of conduct](CODE_OF_CONDUCT.md) | Community standards and enforcement |
| [License](LICENSE) | MIT License |

## License

Copyright (c) 2026 Rethunk Tech. Licensed under the MIT License — see [LICENSE](LICENSE) for details.
