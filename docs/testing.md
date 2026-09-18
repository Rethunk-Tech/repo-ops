# Verification and Testing

Full dev verification commands and CI configuration for `repo-ops`.

## Full dev verification

```bash
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check . --project-excludes 'tests/**' 'scripts/**'
uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=78
uv build
```

`pyrefly check .` runs in single-file mode, which ignores `project-includes`/`project-excludes` from the config file entirely — the command-line `--project-excludes` flag is what actually keeps `tests/`/`scripts/` out of scope. Root `pyproject.toml` `[tool.pyrefly]` is authoritative where it and the CI YAML diverge. Hatch fast verification forwards each workspace's configured values rather than hard-coding paths.

## Test layout

Every test lives in `tests/` — one `testpaths` entry. A bare `uv run pytest` runs the blocking
non-integration lane; pass `-m integration` to run the advisory integration lane.

Config: root `pyproject.toml` `[tool.pytest.ini_options]`. CI runs the same checks on Python 3.12–3.14; tag `v*` triggers [release.yml](../.github/workflows/release.yml).

## Coverage floor

Current ratchet: **78 %**; a raise sets the floor to `floor(actual) − 5`. Update `[tool.coverage.report] fail_under` in `pyproject.toml` and `--cov-fail-under` in `.github/workflows/verify.yml` together.

Coverage source (`[tool.coverage.run] source` in root `pyproject.toml`) is `src`, which covers every module in the package.

## Test markers

The `integration` marker flags tests that require real external tools (bun, yarn, npm registry, go, git network calls). CI splits these into two jobs in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml):

| Job | Marker | Blocking? | Toolchain setup |
| ----- | -------- | ----------- | ----------------- |
| `ci` | `-m "not integration"` | Yes (PR gate) | Python matrix only |
| `integration` | `-m integration` | No (`continue-on-error: true`) | Node 24 + Corepack (yarn), bun, Go stable |

The advisory job runs only `pytest -m integration` — not the blocking lint, pyrefly, or coverage gates. To match its toolchain locally:

```bash
corepack enable
uv run pytest -q -m integration
```
