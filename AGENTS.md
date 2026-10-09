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
- Config is two files and four keys ([docs/usage.md § Config](docs/usage.md#config)). No env vars; a new knob needs a measured case first.

## Release

Tag `v*` on `main`; [release.yml](.github/workflows/release.yml) builds and attaches the wheel. After changing the CLI, reinstall from committed HEAD: `uv tool install --reinstall "git+file://$PWD@$(git rev-parse HEAD)"`.

## Gate budget

Measured 2026-10-09 at load 10-14 on 32 cores. pytest runs under xdist, and `-n auto` alone spawned 32 workers: warm `gate --profile` cost 46 s CPU for 5.6 s wall. `--maxprocesses=8` in `addopts` cut it to 12.6 s CPU and 4.2 s wall; keep the cap. Cold (fresh copy, new `.venv`, throwaway uv cache) is 6.3 s wall and 21.7 s CPU. Both are under the 10 s warm and 30 s cold bar. pytest with coverage is the slowest step; no step is duplicated between `.gate.toml` and the project.
