# TODO

Work that is decided but not built. `AGENTS.md` and `docs/` describe what
exists; this file is the only place that describes what does not. When an item
ships, delete its section.

## NuGet is not a bumper until the manifests are counted

NuGet/dotnet is not an ecosystem `repo-ops` bumps. A bumper is added only after
a count of real manifests under the scan roots is in the same league as an
ecosystem already registered. Otherwise record measured-not-worth in this
section and leave the registry alone.

### Read

- `README.md:15` lists what `apply` bumps: Node, Go, Rust, Python, Deno,
  containers, pre-commit, submodules, and GitHub Actions pins. NuGet/dotnet is
  absent.
- Registration is `register_all()` in `src/repo_ops/_register.py:37-52` (the
  `plugins` dict) plus `register_precommit()` at `_register.py:15`. No NuGet or
  dotnet name is registered. `docs/architecture.md:10` lists the check/apply
  plugin packages. Actions and submodule pins are sweep paths under
  `repo_ops_upgrade/`, not entries in that dict.
- Scan roots, when the CLI omits them, are `DEFAULT_ROOTS` in
  `src/repo_ops_core/constants.py:46-51`.
- The gh-actions measurement is not this count. `gh-actions/AGENTS.md:63`
  records two CI callers (`mortar-bepinex-bridge` and `mortar-smapi-bridge`),
  each a one-line `actions/setup-dotnet` passthrough. That is a composite-action
  caller count.

### Traps

- Test fixtures are not manifests. A `*.csproj`, `*.fsproj`, `*.vbproj`, or
  `Directory.Packages.props` under a repo's tests or a repo-ops fixture is a
  sample, not a dependency to bump.
- `packages.lock.json` is NuGet's lockfile. Do not treat it as a manifest and
  do not rewrite it blindly; a real bumper would edit project manifests and
  regenerate the lock the way other ecosystems do.

### Acceptance

1. Count NuGet and dotnet project manifests under the scan roots, grouped by
   repo. Report the count. Do not invent it, and do not reuse the two-caller
   `setup-dotnet` figure as a stand-in.
2. If that count is in the same league as an ecosystem repo-ops already bumps,
   write the bumper spec here: discovery globs, the lockfile rule above, the
   checker/applier pair, and the `register_all()` name. Then build it.
3. Otherwise write measured-not-worth into this section and do not add an
   ecosystem, a plugin package, or a README line.

## container verify is a no-op

`verify_workspace` returns `None` for `manager == "container"` with a
comment that image builds are out of scope. An apply that rewrites a
FROM line is therefore always "verified". Completing this means a
policy (pull? `docker build`? digest check?) and a network dependency
inside the apply verify budget. Not a small gap.

### Read

1. `src/repo_ops_upgrade/verify.py:258` the container fall-through.
2. `src/repo_ops_container/container.py` apply docstring (v1 no build).

### Traps

- A pull of every bumped image on a fleet sweep is unbounded time and
  disk. Any verify must be explicit and probably opt-in, which is a
  config key — forbidden without a measured case.

### Acceptance

- Decided in a later spec: either keep the no-op and document it as
  intentional in `docs/usage.md`, or add a bounded check that does not
  require a new config key. Do not invent `REPO_OPS_CONTAINER_*` here.
