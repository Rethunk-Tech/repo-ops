<h1 align="center">repo-ops</h1>

<div align="center">

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml)

Local repository operations and dependency maintenance tools for Rethunk-Tech

</div>

---

repo-ops packages four console workflows for scanning many local git checkouts under shared roots: dependency footprint reporting, read-only upgrade planning, apply-with-verify upgrades (including GitHub Actions pin bumps), and push-reflog audits.

The tools share discovery, git, and subprocess helpers in `repo_ops.core`, expose domain logic under `inventory/`, `upgrade/`, and `pushed/`, and ship as PEP 621 console scripts (install and usage: [HUMANS.md](HUMANS.md)). They target monorepo-style layouts where you maintain sibling org checkouts rather than a single application tree.

## Highlights

- `repos-inventory`, `repos-upgrade-plan`, `repos-upgrade-apply`, and `repos-pushed` console tools, plus optional `repo-ops` dispatcher
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
