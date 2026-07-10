# Architecture

Package layout and filesystem map for the `repo-ops` uv workspace under `packages/`.

## Layout and files

- `packages/repo-ops-core/` — shared constants, git, filesystem, process, CLI helpers (`repo_ops_core`)
- `packages/repo-ops-models/` — dataclasses (`repo_ops_models`)
- `packages/repo-ops-lockfiles/` — lockfile parsers and snapshot diff; also hosts the `sbom` and `license` subcommands (`repo_ops_lockfiles`)
- `packages/repo-ops-js/` — Node workspace helpers and overrides audit (`repo_ops_js`)
- `packages/repo-ops-js-{npm,pnpm,bun,yarn}/` — PM check/apply plugins with `register()`
- `packages/repo-ops-go/`, `repo-ops-rust/`, `repo-ops-py*/` — ecosystem plugins
- `packages/repo-ops-container/` — Dockerfile/compose image pin check and apply (`repo_ops_container`)
- `packages/repo-ops-upgrade/` — plan/apply (`run.py`), `discovery`, `orchestration`, `registry`, `actions`, `verify`, `report`, `post_apply`
- `packages/repo-ops-upgrade-remediations-{core,js,go,py,rust}/` — per-ecosystem remediations
- `packages/repo-ops-inventory/`, `repo-ops-pushed/` — inventory and push audit
- `packages/repo-ops-audit/` — fleet vulnerability sweep across ecosystems (`repo_ops_audit`)
- `packages/repo-ops-dependabot/` — list/close Dependabot PRs superseded by local manifests (`repo_ops_dependabot`)
- `packages/repo-ops/` — meta CLI (`repo_ops.cli`, `repo_ops._register`, bundled skill under `repo_ops/skills/`)
- `tests/` — unit tests with mocked subprocess (pure-unit tier); a git-touching tier exercises real git operations; future `integration` tests (bun/yarn/go network calls) will carry the `@pytest.mark.integration` marker. `packages/repo-ops-lockfiles/tests/` — parser unit tests
- Root `pyproject.toml` / `uv.lock` — workspace manifest only

Ecosystem plugins register via `repo_ops._register.register_all()` before CLI dispatch. Programmatic import paths: [docs/programmatic-imports.md](programmatic-imports.md). Do not add top-level scripts; route through workspace packages and the `repo-ops` entry point.
