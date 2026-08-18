# Bulk Maintenance

Running `plan`/`apply` across many checkouts under a parent root, and the submodule-umbrella ordering that needs.

## Playbook

**1. Plan first and keep the output.** Apply never substitutes for plan.

```bash
repo-ops plan /path/to/parent/root --log-file plan.log
repo-ops plan --json --json-out plan.json /path/to/parent/root   # machine-readable
```

Prefer `--log-file` over a shell pipe: it keeps the process exit code. If you do pipe, `set -o pipefail` first, or `tee` exiting 0 masks a failed run.

**2. Review dirty skips.** Repos with uncommitted changes are skipped — look for `dirty git worktree` in plan JSON or stderr. Commit, stash, or re-run with `--allow-dirty` when applying on a dirty tree is intended.

**3. Apply.** Output ends with a **Summary** roll-up (workspace ok/fail/skip, dirty skips, verify failures, actions bumps), a **Failures** list capped by `--max-failures` (default 20), then `failures:` and `exit_code:` for automation. JSON carries the same counts under `summary`. `--strict-exit` also exits `1` on `skip`, which otherwise does not fail the run.

**4. Verify failures are not always bad bumps.** Apply runs ecosystem build/test gates, so a failure may be a missing env var (Next.js `build` needing secrets), a flaky test, or a toolchain gap. Read the log before reverting. `--on-verify-fail=revert` (default) rolls back apply artifacts for failed workspaces; `keep` preserves them for triage. Output sets `verify_fail_disposition` to `FAILED_WITH_CHANGES` or `FAILED_CLEAN`.

**5. Optionally commit.** After a green apply:

| Flag | Meaning |
| ------ | --------- |
| `--commit=none` | Default: no commits. |
| `--commit=conventional` | One `chore(deps): …` commit per repo via guarded local `git`. |
| `--commit=agent` | Draft the subject via a commit-agent provider (default [Cursor SDK](https://cursor.com/docs/sdk/python)); needs `CURSOR_API_KEY` and `uv pip install 'repo-ops[commit-agent-cursor]'`. |
| `--commit-dry-run` | Print intended messages and file lists; no commit. |
| `--commit-on-fail` | Commit even when verify failed (default: skip failed repos). |

**Guardrails:** skips repos dirty at start unless `--allow-dirty`; never stages `.env`, credential-like paths, or key material; fails closed when verify failed.

**Branch / PR mode:** `--pr` (with optional `--pr-base`, `--branch-prefix`) pushes a branch and opens a PR via authenticated `gh` instead of leaving commits on the current branch — [usage.md § Branch / PR workflow](usage.md#branch--pr-workflow-apply).

## Submodule umbrellas

Umbrella repos (Bastion-style, many Go/Node submodules) need **children-first** ordering: apply and push each child, then commit the parent's submodule pointer, then push the parent. Both halves are on by default — `--submodules` reads `.gitmodules` for parent/child links, and `--submodule-order=children-first` orders the apply. The same dependency-first rule as the `git-push-order` skill.

```bash
repo-ops plan --json --json-out plan.json /path/to/Bastion
repo-ops apply --commit=conventional --commit-umbrella /path/to/Bastion --log-file apply.log
```

`--commit-umbrella` enables the parent pointer commits only — it does not select child-first processing, and it still requires `--commit`. Plan/apply JSON gains `submodules: [{path, git_repo, parent}]` unless `--no-submodules` is passed.

Then push children first, and push the parent last. If a parent pointer is still dirty afterwards, apply reports it via `submodule_pointer_hints`; stage the submodule path and commit it before pushing the parent.
