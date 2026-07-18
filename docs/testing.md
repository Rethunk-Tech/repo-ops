# Verification and Testing

Full dev verification commands and CI configuration for `repo-ops`.

## Full dev verification

```bash
uv sync --locked --group dev
uv run python scripts/check_import_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/repo-ops-core/src packages/repo-ops-models/src packages/repo-ops-lockfiles/src packages/repo-ops-js/src packages/repo-ops-js-npm/src packages/repo-ops-js-pnpm/src packages/repo-ops-js-bun/src packages/repo-ops-js-yarn/src packages/repo-ops-go/src packages/repo-ops-rust/src packages/repo-ops-container/src packages/repo-ops-py/src packages/repo-ops-py-uv/src packages/repo-ops-py-poetry/src packages/repo-ops-py-pip-tools/src packages/repo-ops-py-hatch/src packages/repo-ops-py-pdm/src packages/repo-ops-py-pipenv/src packages/repo-ops-upgrade/src packages/repo-ops-upgrade-remediations-core/src packages/repo-ops-upgrade-remediations-js/src packages/repo-ops-upgrade-remediations-go/src packages/repo-ops-upgrade-remediations-py/src packages/repo-ops-upgrade-remediations-rust/src packages/repo-ops-upgrade-remediations-deno/src packages/repo-ops-inventory/src packages/repo-ops-pushed/src packages/repo-ops-audit/src packages/repo-ops-dependabot/src packages/repo-ops-doctor/src packages/repo-ops/src
uv run pytest -q -m "not integration" --cov --cov-report=term-missing --cov-fail-under=68 tests packages/repo-ops-audit/tests packages/repo-ops-lockfiles/tests packages/repo-ops-py-pdm/tests packages/repo-ops-py-pipenv/tests
uv build packages/repo-ops
```

The mypy path list mirrors `[tool.mypy] packages` in root `pyproject.toml` (authoritative when they diverge from CI YAML).

## Test layout

| Path | Role |
|------|------|
| `tests/` | Root unit and integration tests (see markers below) |
| `packages/repo-ops-lockfiles/tests/` | Lockfile parser unit tests (in `[tool.pytest.ini_options] testpaths`) |
| `packages/repo-ops-audit/tests/` | Audit subcommand tests (34+ cases; CI/release pass this path explicitly) |
| `packages/repo-ops-py-pdm/tests/` | PDM plugin smoke tests (`register`, `check_pdm`, `apply_pdm`) |
| `packages/repo-ops-py-pipenv/tests/` | Pipenv plugin smoke tests (`register`, `check_pipenv`, `apply_pipenv`) |

Config: root `pyproject.toml` `[tool.pytest.ini_options]`. CI runs the same checks on Python 3.10–3.13 (3.14 is an allowed failure); tag `v*` triggers [release.yml](../.github/workflows/release.yml). When changing Markdown tiers, run the doc-audit skill on the repo root (e.g. `~/.cursor/skills/doc-audit` or `~/.claude/skills/doc-audit`).

## Coverage floor

Current ratchet: **68 %** (raised from 63 % in v0.17.2; floor = floor(actual) − 5 at time of raise). Update `[tool.coverage.report] fail_under` in `pyproject.toml` and `--cov-fail-under` in `.github/workflows/ci.yml` together whenever the floor is raised.

Coverage source (`[tool.coverage.run] source` in root `pyproject.toml`) includes all workspace packages, including `repo-ops-py-pdm`, `repo-ops-py-pipenv`, and the remediations packages (`remediations-core`, `-js`, `-py`, `-go`, `-rust`, `-deno`).

## Test markers

The `integration` marker flags tests that require real external tools (bun, yarn, npm registry, go, govulncheck audit, git network calls). CI splits these into two jobs in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml):

| Job | Marker | Blocking? | Toolchain setup |
|-----|--------|-----------|-----------------|
| `ci` | `-m "not integration"` | Yes (PR gate) | Python matrix only |
| `integration` | `-m integration` | No (`continue-on-error: true`) | Node 22 + Corepack (yarn), bun, Go stable, govulncheck |

The advisory `integration` job installs govulncheck (`go install golang.org/x/vuln/cmd/govulncheck@latest`), enables Corepack for yarn, and runs only `pytest -m integration` — it does not run the blocking lint, mypy, or coverage gates.

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
