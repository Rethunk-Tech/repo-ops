<h1 align="center">repo-ops</h1>

<div align="center">

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml)

Local repository operations and dependency maintenance tools for Rethunk-Tech

</div>

---

repo-ops is a Python CLI for scanning local git checkouts: dependency footprint, read-only upgrade planning, apply-with-verify upgrades (including GitHub Actions pins), and push-reflog audits. See [releases](https://github.com/Rethunk-Tech/repo-ops/releases) for the latest version ([changelog](CHANGELOG.md)).

## Quick start

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git"
repo-ops doctor
```

Full install, requirements, and operator runbook: [HUMANS.md](HUMANS.md).

## Highlights

- Single CLI: `inventory`, `plan`, `apply`, `pushed`, `audit`, `dependabot`, `sbom`, `license`, `doctor`, and `install-skill` (bundled agent skill for Cursor/Claude)
- Plan is read-only; apply runs ecosystem updates, optional Corepack for Node, and verify gates
- GitHub Actions pin drift in plan/apply by default (`--skip-actions` to omit); actions-only repos (workflows, no manifests) included
- Discovery: validated scan roots, optional `--follow-symlinks`, `--flat` and actions-only repos; full-coverage cross-range sweep by default, `--conservative` to stay in-range (see [usage](docs/usage.md#ecosystem-upgrade-modes-plan-and-apply))
- uv-locked, single package (`uv_build`); Python >= 3.11; ecosystem plugins (Node, Go, Rust, Python) with remediations
- Built for Rethunk-Tech monorepo and sibling checkout trees under shared roots

## Documentation

| Document | Purpose |
| ---------- | --------- |
| [Humans guide](HUMANS.md) | Quickstart, requirements, and doc index |
| [Agents guide](AGENTS.md) | Agent quick reference, coordination, and workflows |
| [Contributing](CONTRIBUTING.md) | Contributor guidelines and code style |
| [Security](SECURITY.md) | Vulnerability reporting |
| [Changelog](CHANGELOG.md) | Release history (Keep a Changelog style) |
| [Code of conduct](CODE_OF_CONDUCT.md) | Community standards and enforcement |
| [License](LICENSE) | MIT License |

## License

Copyright (c) 2026 Rethunk Tech. Licensed under the MIT License — see [LICENSE](LICENSE) for details.
