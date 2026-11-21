# repo-ops contributor guide

Changing repo-ops itself. Running it: [README.md](README.md) and `repo-ops <cmd> --help`.

## Layout

One wheel; every `src/repo_ops_*` package ships in it (`module-name` in `pyproject.toml`). Module map: [docs/architecture.md](docs/architecture.md). A new ecosystem plugin registers in `repo_ops._register`.

## Gate

`gate` runs `.gate.toml`: ruff, pyrefly, pytest, deptry, `uv audit`, actionlint. Run `uv run ruff format .` before it. Integration lane: `uv run pytest -q -m integration`. Detail: [docs/testing.md](docs/testing.md).

## Invariants

- `plan` writes nothing to lockfiles, manifests or workflows; Yarn checks may run `corepack prepare`.
- Commits stage only the files apply touched (`git commit -- <paths>`), never `.env` or key material, and never push.
- SHA-pinned Actions and images stay pinned: digests advance, never revert to floating tags.
- Repo build/verify commands go through `repo_ops_core.process.run`, which redirects user-global tool config writes to the run dir (`isolate_user_config`); repo-ops' own `git` calls are exempt. A new spawn path for repo commands uses `run`.
- Config is two files and six keys ([docs/usage.md § Config](docs/usage.md#config)). No env vars; a new knob needs a measured case first.
- With no named root, commands scan the lines of `bosun roots` (bosun on `PATH`), else the built-in list in `repo_ops_core/constants.py`; it is looked up only when a scan needs it, never at import.

## Release

Tag `v*` on `main`; [release.yml](.github/workflows/release.yml) builds and attaches the wheel. After changing the CLI, reinstall from committed HEAD: `uv tool install --reinstall "git+file://$PWD@$(git rev-parse HEAD)"`.

## Gate budget

Budget: 10 s warm, 30 s cold. pytest runs under xdist with `--maxprocesses=8` in `addopts`: `-n auto` alone spawns a worker per core and costs several times the CPU for barely less wall time, so keep the cap. Coverage (floor: `fail_under` in `pyproject.toml`) runs in the gate and in `verify.yml`; no step is duplicated between `.gate.toml` and the project.
