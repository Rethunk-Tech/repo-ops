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

## collect_changes_after_apply does not parse Deno manifests

`collect_changes_after_apply` now includes `DENO_LOCKFILES`, so a dirty
`deno.lock` becomes a coarse `Change(fname, "changed", "updated")`. A
`deno.json` / `deno.jsonc` import-map bump is invisible: those basenames
are not in the lock-name list, and there is no Deno analogue of the
`package.json` dependency-section walk.

Remediations that key off `ctx["changes"]` after a Deno apply therefore
see lockfile-only updates and miss import-map-only applies.

Keep the existing package.json walker and the coarse lockfile fallback.
Do not invent a second JSON walker that treats every string value as a
package. Deno import maps mix URLs, `npm:`, `jsr:`, and path aliases.

### Read

1. `src/repo_ops_upgrade/post_apply.py:52` `collect_changes_after_apply` —
   lock names at `:67`, package.json walk at `:86`, coarse lockfile
   `Change` at `:129`.
2. `src/repo_ops_lockfiles/constants.py:21` `DENO_LOCKFILES` is
   `("deno.lock",)` only.
3. `src/repo_ops_js/deno.py` apply/check — which fields of `deno.json`
   an apply actually rewrites (imports vs tasks vs compilerOptions).

### Traps

- A `deno.json` `"tasks"` or `"compilerOptions"` edit is not a dependency
  change. Walking every added JSON string would report them as packages.
- `deno.jsonc` is JSON-with-comments; `json.loads` fails. The package.json
  walker is line-oriented on `git diff` and does not parse JSONC either.
- Coarse `Change("deno.json", "changed", "updated")` is enough for
  remediations that only need a non-empty `changes` list. Per-package
  names are only needed if a Deno remediation filters by package.

### Acceptance

- A git diff that only adds an import in `deno.json` or `deno.jsonc`
  yields at least one `Change` from `collect_changes_after_apply`.
- A git diff that only changes `"tasks"` does not invent a package name
  from the task string.
- `package.json` dependency extraction and coarse lockfile rows stay
  identical. Existing collect-changes tests stay green.

## bun.lockb is not a commit-scope or classifier name

`NODE_LOCKFILES` and `classify_commit_subject` know `bun.lock` (Bun ≥1.0
text). A leftover binary `bun.lockb` at the repo root is not in that
tuple, so a root Bun apply that still writes `bun.lockb` is unscoped
(commit skip) and classified as the generic deps subject.

This is only worth building if a measured checkout under `DEFAULT_ROOTS`
still has `bun.lockb` and no `bun.lock`. Otherwise record
measured-not-worth here.

### Read

1. `src/repo_ops_lockfiles/constants.py:5` `NODE_LOCKFILES`.
2. `src/repo_ops_upgrade/post_apply_commit.py:80` `_lockfile_names_for_manager`
   (`bun` → `NODE_LOCKFILES`) and `:370` `classify_commit_subject` (`bun.lock`
   in the Node basename set, no `bun.lockb`).
3. Bun apply in `src/repo_ops_js_bun/bun.py` — which lockfile name it writes.

### Traps

- Adding `bun.lockb` to `NODE_LOCKFILES` also changes plan-time snapshots
  and collect-changes. A repo that has both files must not double-commit
  unrelated dirt on the binary file if apply only rewrote the text lock.
- `classify_commit_subject` compares `Path(p).name.lower()`, so
  `bun.lockb` is a whole-name match, not a suffix.

### Acceptance

- If `bun.lockb` is still written: a root Bun workspace with only that
  file dirty stages it, and the subject is the Node lockfile subject.
- If it is not written anywhere under the scan roots: this section says
  measured-not-worth and the tuple is unchanged.

## `--filter` has no manager counterpart

`filter_workspaces` already accepts `manager_filter` and applies it when
set. `run_upgrade` always passes `None`. `--filter` is ecosystem-only
(`ECO_FILTER_CHOICES`). A caller who wants `uv` but not `poetry` cannot
say so without a new flag.

Deferred because a new public flag is a CLI contract, and the brief
forbids changing existing contracts. Shape is decided: do not overload
`--filter` with manager names (`uv` is not in `ECO_FILTER_CHOICES`; `py`
is). Add a separate `--manager` with the registered plugin names.

### Read

1. `src/repo_ops_upgrade/orchestration.py:246` `filter_workspaces`.
2. `src/repo_ops_upgrade/run.py:137` call site, third arg `None`.
3. `src/repo_ops_core/cli.py:24` `ECO_FILTER_CHOICES` and `:160` `--filter`.
4. `src/repo_ops/_register.py:37` plugin manager names.

### Traps

- `container`, `precommit`, `actions`, and `submodules` are ecosystems
  (or sweep paths), not rows in the plugin dict. `--manager` must not
  silently accept them as managers.
- `--filter py --manager npm` is empty, not an error today. Decide:
  empty result exit 0 vs argparse error. Empty 0 matches `--filter`
  with no matching workspace.

### Acceptance

- `repo-ops apply --manager uv PATH` processes only `manager == "uv"`
  workspaces. Other Python managers in that checkout are untouched.
- `--filter` choices, help text, and current matching are unchanged.
- `repo-ops apply --help` documents `--manager`. Plan gets the same flag
  because it shares `build_upgrade_parser`.

## classify_commit_subject ignores container files once any lockfile is present

A Dockerfile-only commit now gets `chore(deps): update container image
pins`. A mixed list (`yarn.lock` + `Dockerfile`, or a workflow +
`Dockerfile`) still classifies as if the container file were absent, so
a Node+container apply is attributed to Node alone and an actions-only
subject can hide a digest bump.

Completing that enumerates container in `dep_parts` the way Deno is
enumerated. That **changes existing mixed-commit subjects**, which is
why it is deferred.

### Read

1. `src/repo_ops_upgrade/post_apply_commit.py:370` `classify_commit_subject`.
2. `src/repo_ops_upgrade/post_apply_commit.py` `_is_container_basename`.
3. `tests/test_post_apply_commit.py` mixed-subject cases around the
   `classify_commit_subject multi-ecosystem` section.

### Traps

- `compose.yaml` at repo root is a container file; `compose.yaml` under
  `.github/` is not a workflow unless the path also contains `.github`.
  Keep the existing `has_ci` predicate (basename yaml **and** `.github`
  in the path).
- `Dockerfile.md` matches the same prefix rule discovery already uses.
  Tightening the suffix is a different change.

### Acceptance

- `["yarn.lock", "Dockerfile"]` mentions both Node and container image
  pins. `[".github/workflows/ci.yml", "Dockerfile"]` is mixed deps+ci,
  not `chore(ci)` only.
- `["yarn.lock"]` and `[".github/workflows/ci.yml"]` subjects are
  byte-identical to today.

## precommit apply still uses the generic commit subject

Root `.pre-commit-config.yaml` is now in commit scope. The subject for
that path alone is still `chore(deps): apply repo-ops dependency
updates` because `classify_commit_subject` has no precommit basenames.

Adding a `precommit` part changes that subject string (a contract for
anyone matching it) and must decide `chore(deps)` vs `chore(hooks)`.

### Read

1. `src/repo_ops_upgrade/post_apply_commit.py:370` `classify_commit_subject`.
2. `src/repo_ops_upgrade/post_apply_commit.py` `_manifest_names_for_manager`
   for `precommit`.
3. `src/repo_ops_upgrade/precommit.py:41` the two config basenames.

### Traps

- Only `.pre-commit-config.yaml` and `.pre-commit-config.yml`. Do not
  match `.pre-commit-hooks.yaml` (that's an upstream hook repo's
  hook definition, not this checkout's pin file).

### Acceptance

- `classify_commit_subject([".pre-commit-config.yaml"])` is a dedicated
  conventional subject, documented next to the other single-ecosystem
  strings in `test_classify_single_ecosystem_unchanged`.
- Mixed precommit + Node enumerates both. Node-only and CI-only strings
  stay identical.

## Cargo.toml edits are invisible to collect_changes_after_apply

`RUST_LOCKFILES` is `("Cargo.lock",)`. A rust apply that rewrites
version reqs in `Cargo.toml` without a lockfile byte change yields no
post-apply `Change` rows. Commit scope already includes `Cargo.toml` via
`_manifest_names_for_manager`. This gap is remediation context only.

Do not add `Cargo.toml` to `RUST_LOCKFILES`: that tuple is also the
snapshot set, and treating the manifest as a coarse lockfile would emit
`Change("Cargo.toml", "changed", "updated")` instead of per-crate rows.

### Read

1. `src/repo_ops_lockfiles/constants.py:20` `RUST_LOCKFILES`.
2. `src/repo_ops_upgrade/post_apply.py:52` `collect_changes_after_apply`.
3. `src/repo_ops_rust/rust.py` `apply_rust` — whether it rewrites
   `Cargo.toml` or only runs `cargo update`.

### Traps

- Coarse-diff on `Cargo.toml` is the wrong shape if rust remediations
  ever filter by crate name.
- Workspace member `Cargo.toml` paths are not basenames at repo root;
  subdirectory workspaces already match by prefix in commit scope.

### Acceptance

- If `apply_rust` never writes `Cargo.toml`, write measured-not-worth
  and stop. If it does, a toml-only diff yields at least one `Change`
  and `Cargo.lock`-only diffs stay coarse lockfile rows.

## `--no-verify` implies `--no-commit` with no message

`commit = apply and not no_commit and not no_verify`. `--no-verify`
silently drops the post-apply commit. Help text says it implies
`--no-commit`. A user who passed only `--no-verify` and expected a
commit of unverified changes gets neither a commit nor a reason on
stderr.

Printing a one-line skip is an output-contract change.

### Read

1. `src/repo_ops_upgrade/run.py:126` commit gate.
2. `src/repo_ops_core/cli.py:173` `--no-verify` help.
3. `src/repo_ops_upgrade/post_apply_commit.py:609` `_print_outcome`
   skip/fail/committed prefixes.

### Traps

- Do not commit unverified trees. The implication stays.
- Fleet vs named-root does not change this; both honor the flag.
- `--no-commit --no-verify` must not print the note twice.

### Acceptance

- `repo-ops apply --no-verify PATH` still does not commit.
- stderr (or the existing `[commit skip]` stream) contains one line that
  says verify was skipped so commit was skipped, unless `--no-commit`
  was also passed.
- Exit codes and report.json fields stay the same.

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
