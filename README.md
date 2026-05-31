<h1 align="center">repo-ops</h1>

<div align="center">

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml)

Local repository operations and dependency maintenance tools for Rethunk-Tech

</div>

---

repo-ops is a **uv workspace** of Python packages for scanning local git checkouts: dependency footprint, read-only upgrade planning, apply-with-verify upgrades (including GitHub Actions pins), and push-reflog audits.

Install and run the unified **`repo-ops`** CLI — see [HUMANS.md](HUMANS.md). Upgrading from 0.2.x `repos-*` entry points: [HUMANS — Migration from 0.2.x](HUMANS.md#migration-from-02x).

## Highlights

- Single CLI: `inventory`, `plan`, `apply`, `pushed`, and `install-skill` (bundled agent skill for Cursor/Claude)
- Upgrade plan/apply includes GitHub Actions pin drift by default (`--skip-actions` to omit)
- uv-locked workspace (`uv_build`); Python >= 3.10
- Ecosystem plugins (Node, Go, Rust, Python) with remediations and verify gates
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
