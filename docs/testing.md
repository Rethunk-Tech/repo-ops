# Verification and Testing

Full dev verification commands and CI configuration for `repo-ops`.

## Full dev verification

```bash
uv sync --locked --group dev
uv run python scripts/check_import_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/repo-ops-core/src packages/repo-ops-models/src packages/repo-ops-lockfiles/src packages/repo-ops-js/src packages/repo-ops-js-npm/src packages/repo-ops-js-pnpm/src packages/repo-ops-js-bun/src packages/repo-ops-js-yarn/src packages/repo-ops-go/src packages/repo-ops-rust/src packages/repo-ops-py/src packages/repo-ops-py-uv/src packages/repo-ops-py-poetry/src packages/repo-ops-py-pip-tools/src packages/repo-ops-py-hatch/src packages/repo-ops-upgrade/src packages/repo-ops-upgrade-remediations-js/src packages/repo-ops-inventory/src packages/repo-ops-pushed/src packages/repo-ops/src
uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=15
uv build packages/repo-ops
```

Tests: `tests/`; config in root `pyproject.toml`. CI runs the same checks on Python 3.10–3.12; tag `v*` triggers [release.yml](../.github/workflows/release.yml). When changing Markdown tiers, run the doc-audit skill on the repo root (e.g. `~/.cursor/skills/doc-audit` or `~/.claude/skills/doc-audit`).

Pull requests welcome via repository issues.
