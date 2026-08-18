# Architecture

Module layout and filesystem map for `repo-ops`.

The repository builds **one distribution** (`repo-ops`). The top-level `repo_ops_*`
modules under `src/` are a code-layout choice, not separate installables — they all
ship in the same wheel, listed in `pyproject.toml`'s `[tool.uv.build-backend]
module-name`.

## Layout and files

- `src/repo_ops_core/` — shared constants, git, filesystem, process, CLI helpers, hold rules (global/repo version ceilings, including the cached resolver every ecosystem plugin uses), and targeted-bump requirement matching (`requirement.requirement_satisfied` is the caret/range helper — Cargo stays here rather than PEP 440 `SpecifierSet`)
- `src/repo_ops_models/` — dataclasses
- `src/repo_ops_lockfiles/` — lockfile parsers (`versions_*` including go.sum, Cargo.lock, requirements.txt) and snapshot diff; also hosts the `sbom` and `license` subcommands
- `src/repo_ops_js/` — Node workspace helpers, overrides audit, Deno support, and the shared audit-override store
- `src/repo_ops_js_{npm,pnpm,bun,yarn}/` — PM check/apply plugins with `register()`
- `src/repo_ops_go/`, `src/repo_ops_rust/` — Go and Rust check/apply plugins
- `src/repo_ops_py/` — Python workspace dispatch, plus the shared PyPI lookup and hold filter used by the PM plugins
- `src/repo_ops_py_{uv,poetry,pip_tools,hatch,pdm,pipenv}/` — Python PM check/apply plugins with `register()`
- `src/repo_ops_container/` — Dockerfile/compose image pin check and apply
- `src/repo_ops_upgrade/` — plan/apply (`run.py`), `discovery`, `orchestration`, `registry`, `actions`, `verify`, `report`, `post_apply`, and `tags` (shared git-ls-remote tag resolution for submodule and pre-commit pins)
- `src/repo_ops_upgrade_remediations_{core,js,go,py,rust,deno}/` — per-ecosystem remediations
- `src/repo_ops_inventory/`, `src/repo_ops_pushed/` — inventory and push audit
- `src/repo_ops_status/` — per-repo branch/dirty/ahead-behind collection and rendering (`inventory --status`)
- `src/repo_ops_audit/` — fleet vulnerability sweep across ecosystems
- `src/repo_ops_dependabot/` — list/close Dependabot PRs superseded by local manifests
- `src/repo_ops_doctor/` — toolchain/auth preflight: probe known binaries and `gh` auth
- `src/repo_ops/` — CLI (`repo_ops.cli`, `repo_ops._register`, bundled skill under `repo_ops/skills/`)
- `tests/` — the whole suite, one `testpaths` entry. Unit tests with mocked subprocess (pure-unit tier); a git-touching tier exercises real git operations; `tests/integration/` holds `@pytest.mark.integration` tests (bun/yarn/go network calls) run via `pytest -m integration` in CI

Ecosystem plugins register via `repo_ops._register.register_all()` before CLI dispatch —
deferred until a subcommand needs them, so `--help` and `doctor` do not import every
plugin. Programmatic import paths: [docs/programmatic-imports.md](programmatic-imports.md).
Do not add top-level scripts; route through the modules and the `repo-ops` entry point.
