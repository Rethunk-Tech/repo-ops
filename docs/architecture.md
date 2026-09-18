# Architecture

One distribution, `repo-ops`. The `repo_ops_*` packages under `src/` are a layout choice; all ship in the same wheel (`module-name` in `pyproject.toml`).

- `repo_ops/`: CLI dispatch (`cli.py`) and plugin registration (`_register.py`).
- `repo_ops_core/`: constants, config, git, filesystem, process, the shared CLI parser, holds, requirement matching, and the state cache.
- `repo_ops_models/`: dataclasses.
- `repo_ops_lockfiles/`: lockfile parsers and snapshot diff.
- `repo_ops_js/`: Node workspace helpers, Expo alignment, Deno support.
- `repo_ops_js_{npm,pnpm,bun,yarn}/`, `repo_ops_go/`, `repo_ops_rust/`, `repo_ops_py_{uv,poetry,pip_tools,hatch,pdm,pipenv}/`, `repo_ops_container/`: check and apply plugins, each exporting `register()`.
- `repo_ops_py/`: Python workspace dispatch, PyPI lookup, hold filter.
- `repo_ops_upgrade/`: `plan`/`apply` (`run.py`), discovery, orchestration, registry, Actions, verify, report, post-apply commits, tag resolution.
- `repo_ops_upgrade_remediations_{core,js,go,py,rust,deno}/`: remediations.
- `repo_ops_dependabot/`: the `dependabot` command.
- `tests/`: the whole suite; `-m integration` selects tests that need real toolchains.

Plugins register in `register_all()` before `plan`/`apply` dispatch, so `--help` imports none of them.
