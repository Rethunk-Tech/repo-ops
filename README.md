<h1 align="center">repo-ops</h1>

<div align="center">

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![CI](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/Rethunk-Tech/repo-ops/actions/workflows/ci.yml)

Dependency maintenance for local git checkouts

</div>

---

`repo-ops apply` finds every checkout under its scan roots, moves each dependency to its newest release (Node, Go, Rust, .NET/NuGet, Python, Deno, containers, pre-commit, submodules and GitHub Actions pins), runs each repo's own gates, and commits every repo that passes. It never pushes. Releases: [GitHub](https://github.com/Rethunk-Tech/repo-ops/releases) ([changelog](CHANGELOG.md)).

## Highlights

- One command: `repo-ops apply` updates, verifies and commits every checkout.
- Newest release everywhere, across ranges and majors, with per-package [holds](docs/usage.md#holds).
- Verifies with each repo's own gates; a fleet failure is reverted, not committed.

## Quick start

Needs [uv](https://docs.astral.sh/uv/), Python 3.12 or newer, and git. Each ecosystem's own CLI (`bun`, `go`, `cargo`, `dotnet`, `uv`, …) is needed only for repos that use it; `gh` enables the Actions org-policy check and `dependabot`.

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git"
repo-ops --version
```

Agent skill, for Claude Code and Cursor:

```bash
bunx skills add Rethunk-Tech/repo-ops
```

Uninstall with `uv tool uninstall repo-ops`.

## Use

```bash
repo-ops apply                    # sweep DEFAULT_ROOTS, commit each verified repo
repo-ops apply /path/to/checkout  # one checkout, even if dirty
repo-ops plan /path/to/checkout   # read-only preview, exit 1 on drift
repo-ops dependabot --close       # close Dependabot PRs the sweep superseded
```

Every run writes a log and `report.json`; their paths are the last line printed.

## Develop

```bash
git clone https://github.com/Rethunk-Tech/repo-ops.git && cd repo-ops
uv sync --group dev
uv run repo-ops plan --help
```

Then [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/testing.md](docs/testing.md). Security reports: [SECURITY.md](SECURITY.md).

## Documentation

| Document | Purpose |
| --- | --- |
| [Usage](docs/usage.md) | What each command does, config, exit codes |
| [Bulk maintenance](docs/bulk-maintenance.md) | Fleet sweeps and submodule umbrellas |
| [Contributor guide](AGENTS.md) | Layout, gate, invariants, release |
| [Changelog](CHANGELOG.md) | Release history |

## License

Copyright (c) 2026 Rethunk Tech. MIT, see [LICENSE](LICENSE).
