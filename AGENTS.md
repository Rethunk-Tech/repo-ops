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
