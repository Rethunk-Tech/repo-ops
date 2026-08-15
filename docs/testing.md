# Verification and Testing

Full dev verification commands and CI configuration for `repo-ops`.

## Full dev verification

```bash
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check . --project-excludes 'tests/**' 'scripts/**'
uv run pytest -q -m "not integration" --cov --cov-report=term-missing --cov-fail-under=68
uv build
```

The pyrefly check scope mirrors `[tool.pyrefly] project-includes` in root `pyproject.toml` (authoritative when they diverge from CI YAML). `pyrefly check .` runs in single-file mode, which ignores `project-includes`/`project-excludes` from the config file entirely — the `--project-excludes` flag on the command line is what actually keeps `tests/`/`scripts/` out of scope, matching what `[tool.mypy] packages` covered before this migration. Hatch fast verification applies the same rule per target workspace, forwarding configured `project-includes` and `project-excludes` values to pyrefly rather than hard-coding repository paths.

## Test layout

Every test lives in `tests/` — one `testpaths` entry, so a bare `uv run pytest` runs the whole suite.

Config: root `pyproject.toml` `[tool.pytest.ini_options]`. CI runs the same checks on Python 3.11–3.13 (3.14 is an allowed failure); tag `v*` triggers [release.yml](../.github/workflows/release.yml). When changing Markdown tiers, run the doc-audit skill on the repo root (e.g. `~/.cursor/skills/doc-audit` or `~/.claude/skills/doc-audit`).

## Coverage floor

Current ratchet: **68 %** (raised from 63 % in v0.17.2; floor = floor(actual) − 5 at time of raise). Update `[tool.coverage.report] fail_under` in `pyproject.toml` and `--cov-fail-under` in `.github/workflows/ci.yml` together whenever the floor is raised.

Coverage source (`[tool.coverage.run] source` in root `pyproject.toml`) is `src`, which covers every module in the package.

## Test markers

The `integration` marker flags tests that require real external tools (bun, yarn, npm registry, go, govulncheck audit, git network calls). CI splits these into two jobs in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml):

| Job | Marker | Blocking? | Toolchain setup |
| ----- | -------- | ----------- | ----------------- |
| `ci` | `-m "not integration"` | Yes (PR gate) | Python matrix only |
| `integration` | `-m integration` | No (`continue-on-error: true`) | Node 22 + Corepack (yarn), bun, Go stable, govulncheck |

The advisory `integration` job installs govulncheck (`go install golang.org/x/vuln/cmd/govulncheck@latest`), enables Corepack for yarn, and runs only `pytest -m integration` — it does not run the blocking lint, pyrefly, or coverage gates.

To skip integration tests during rapid iteration:

```bash
uv run pytest -q -m "not integration"
```

To run integration tests locally (match CI toolchain):

```bash
corepack enable
go install golang.org/x/vuln/cmd/govulncheck@latest
uv run pytest -q -m integration
```

Pull requests welcome via repository issues.
