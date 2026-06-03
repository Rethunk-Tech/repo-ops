<h1 align="center">repo-ops</h1>

<div align="center">

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml)

Local repository operations and dependency maintenance tools for Rethunk-Tech

</div>

---

repo-ops is a **uv workspace** of Python packages for scanning local git checkouts: dependency footprint, read-only upgrade planning, apply-with-verify upgrades (including GitHub Actions pins), and push-reflog audits. Latest release: [0.6.1](CHANGELOG.md#061---2026-06-03) ([changelog](CHANGELOG.md)).

Install and run the unified **`repo-ops`** CLI — [HUMANS.md](HUMANS.md). Operators on legacy `repos-*` entry points: [HUMANS — Migration from 0.2.x](HUMANS.md#migration-from-02x).

## Highlights

- Single CLI: `inventory`, `plan`, `apply`, `pushed`, and `install-skill` (bundled agent skill for Cursor/Claude)
- Plan is read-only; apply runs ecosystem updates, optional Corepack for Node, and verify gates
- GitHub Actions pin drift in plan/apply by default (`--skip-actions` to omit); actions-only repos (workflows, no manifests) included
- Discovery: validated scan roots, optional `--follow-symlinks`, `--flat` and actions-only repos; conservative pnpm/yarn unless env opt-in (see HUMANS)
- uv-locked workspace (`uv_build`); Python >= 3.10; ecosystem plugins (Node, Go, Rust, Python) with remediations
- Built for Rethunk-Tech monorepo and sibling checkout trees under shared roots

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
