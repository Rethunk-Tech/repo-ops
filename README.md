# repo-ops

Local repository operations and dependency maintenance tools for Rethunk-Tech.

This package collects the former top-level scripts from `/usr/local/src/com.github` into importable Python modules and console entry points:

- `repo-dep-count`
- `repo-dep-refresh`
- `repo-pushed-commits`

The first refactor pass is focused on extracting shared root discovery, path filtering, subprocess, Git, and dependency parsing helpers while preserving current command behavior.
