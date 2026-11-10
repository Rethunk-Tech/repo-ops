# Using repo-ops

Install, run, and uninstall. Command flags and exit codes: [docs/usage.md](docs/usage.md).

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

## Uninstall

```bash
uv tool uninstall repo-ops
```
