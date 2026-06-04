# Programmatic Imports

Python module paths for importing `repo-ops` packages programmatically.

| Area | Module |
|------|--------|
| CLI | `repo_ops.cli` |
| Upgrade entrypoints | `repo_ops_upgrade` (`main_plan`, `main_apply`) |
| Models | `repo_ops_models` |
| Discovery | `repo_ops_upgrade.discovery` |
| Ecosystem registry | `repo_ops_upgrade.registry` |
| GitHub Actions pins | `repo_ops_upgrade.actions` |
| Inventory | `repo_ops_inventory.scan` |
| Push audit | `repo_ops_pushed.audit` |
| Lockfiles | `repo_ops_lockfiles` |
| Node helpers | `repo_ops_js` |
| Shared | `repo_ops_core` |

Filesystem layout of `packages/*`: [docs/architecture.md](architecture.md).
