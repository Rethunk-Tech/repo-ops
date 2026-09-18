# TODO

Work that is decided but not built. `AGENTS.md` and `docs/` describe what
exists; this file is the only place that describes what does not. When an item
ships, delete its section.

## PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `repo-ops` wheel once the trusted-publisher claim
is configured. Open issue
[#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

### Read

- `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`);
  `README.md` § Quick start (add the PyPI install line).

### Traps

- **`invalid-publisher` / "valid token, but no corresponding publisher"** for
  claim `sub: repo:Rethunk-Tech/repo-ops:environment:pypi` is a PyPI
  pending-publisher claim mismatch, not a CI defect; do not re-diagnose the
  workflow.

### Acceptance

- A `v*` tag run publishes to PyPI; `uv tool install repo-ops` from PyPI
  matches the GitHub Release version; #124 closed with the publish log.
