# Bulk Maintenance

Submodule umbrella workflows and the multi-step bulk maintenance playbook for running `plan`/`apply` across many checkouts.

## Submodule umbrellas (git-push-order)

Umbrella repos (e.g. Bastion with many Go/Node submodules) need **children-first** apply/push: upgrade and push each submodule checkout, then commit the parent's submodule pointer, then push the parent. `repo-ops` discovers submodule repos via `find .git` like any checkout; `--submodules` reads `.gitmodules` for explicit parent/child links. Aligns with the **git-push-order** skill (dependency-first push, no force unless asked). See [Bulk maintenance](#bulk-maintenance) step 4 for the full playbook.

## Bulk maintenance

Use this flow when running `plan` / `apply` across many checkouts under a parent root (for example `/usr/local/src/com.github`).

1. **Plan first, capture output.** Apply never substitutes for plan; always review drift before writes.

   ```bash
   repo-ops plan /path/to/parent/root --log-file plan.log
   ```

   Or separate stderr: `repo-ops plan /path/to/parent/root 2>plan.stderr | tee plan.log` (requires `set -o pipefail` so a failed `repo-ops` is not masked by `tee` exiting 0).

   Machine-readable plan (discovery notes stay in `skipped` / `warnings`):

   ```bash
   repo-ops plan --json --json-out /path/to/plan.json /path/to/parent/root
   ```

   Or redirect stdout: `repo-ops plan --json /path/to/parent/root > plan.json 2>plan.stderr`

2. **Review dirty skips.** Plan/apply skip repos with uncommitted changes unless you intend otherwise. In plan JSON or human stderr, look for `dirty git worktree`. Per repo: commit, stash, or re-run with `--allow-dirty` when you accept applying on a dirty tree.

3. **Apply with reliable logs.** Prefer built-in logging so the process exit code reflects failures (no shell pipe needed):

   ```bash
   repo-ops apply /path/to/parent/root --log-file apply.log
   ```

   Human and log output end with a **Summary** roll-up (workspace ok/fail/skip, dirty skips, verify failures, actions bumps, discovery skipped), a capped **Failures** list (`--max-failures`, default 20), then `failures:` and `exit_code:` for automation. JSON mode includes the same counts under top-level `summary`. When piping instead, use `set -o pipefail` so a failed `repo-ops` is not masked by `tee` exiting 0:

   ```bash
   set -o pipefail
   repo-ops apply /path/to/parent/root 2>&1 | tee apply.log
   ```

   `--strict-exit` exits `1` when any workspace is `skip` or `fail` (default: `fail` only). Skipped dirty trees do not fail the run unless `--strict-exit` is set.

4. **Submodule umbrellas.** When a parent repo contains submodules (Bastion-style layouts), apply and push **child** checkouts first, then update the **parent** submodule pointer and commit that last. Same dependency-first idea as the [git-push-order](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/bulk-maintenance.md#submodule-umbrellas-git-push-order) agent skill (`~/.claude/skills/git-push-order/SKILL.md`): children before parent pointer.

   ```bash
   repo-ops plan --submodules --json --json-out plan.json /path/to/Bastion
   repo-ops apply --submodule-order=children-first --submodules /path/to/Bastion --log-file apply.log
   ```

   Plan/apply JSON with `--submodules` adds top-level `submodules: [{path, git_repo, parent}]`. After apply, human output and JSON may include `submodule_pointer_hints` when a parent still has dirty submodule gitlinks (commit the parent after children are pushed).

   **Bastion-style example** (umbrella with nested Go/Node submodules under one parent root):

   1. `repo-ops plan --submodules --json-out bastion-plan.json /usr/local/src/com.github/Rethunk-Tech/Bastion` — review `submodules[]` and drift per repo.
   2. `repo-ops apply --submodule-order=children-first --submodules /usr/local/src/com.github/Rethunk-Tech/Bastion --log-file bastion-apply.log` — children apply first; read **Submodule pointers** in the footer for parents needing a pointer commit.
   3. Commit and push each **child** repo (MCP `git-push-order` or `batch_commit` with `push: "after"` on children first).
   4. In the **parent**, stage updated submodule paths and commit (`git add path/to/submodule && git commit -m "chore: bump submodule pointers"`).
   5. Push the parent last.

5. **Verify failures are not always dependency regressions.** Apply runs ecosystem verify gates (build/test). Failures may be missing env vars (for example Next.js `build` needing secrets), flaky tests, or local toolchain gaps — not a bad bump. Inspect logs before reverting. Use `--on-verify-fail=revert` on bulk apply when you want failed workspaces rolled back automatically; default `keep` preserves changes for manual triage. JSON/human output sets `verify_fail_disposition` to `FAILED_WITH_CHANGES` or `FAILED_CLEAN`.

6. **Optional post-apply commits.** After a green apply+verify pass, commit dependency changes in one shot per repo:

   ```bash
   repo-ops apply /path/to/root --commit=conventional --log-file apply.log
   repo-ops apply /path/to/root --commit=agent --commit-dry-run
   ```

   | Flag | Meaning |
   |------|---------|
   | `--commit=none` | Default: no git commits (manual or MCP `batch_commit` in the IDE). |
   | `--commit=conventional` | One `chore(deps): …` (or CI-themed) commit per repo via guarded local `git`. |
   | `--commit=agent` | Draft the subject via a pluggable **commit-agent** provider (default: [Cursor SDK](https://cursor.com/docs/sdk/python)), then commit locally. Requires `CURSOR_API_KEY` and the optional cursor extra. |
   | `--commit-dry-run` | Print intended messages and file lists; no `git commit`. |
   | `--commit-on-fail` | Also attempt commits when verify/apply failed (default: skip failed repos). |

   **Branch / PR mode:** after commits land, pass **`--pr`** (optional **`--pr-base`**, **`--branch-prefix`**) to push a branch and open a PR via `gh` instead of leaving changes on the current branch. Requires authenticated `gh`. See [usage.md — Branch / PR workflow](usage.md#branch--pr-workflow-apply).

   **Commit-agent providers:** default `cursor` when `REPO_OPS_COMMIT_AGENT_PROVIDER` is unset. Additional providers can register via `repo_ops_upgrade.commit_agent_providers.register_commit_agent_provider`. **Guardrails:** skips repos that were dirty at start unless `--allow-dirty`; never stages `.env`, credential-like paths, or key material; fails closed when verify failed (unless `--commit-on-fail`). Install Cursor support: `uv pip install 'repo-ops-upgrade[commit-agent-cursor]'` (alias: `[agent]`).

   **IDE agents:** prefer MCP **`plugin-rethunk-git-git`** `batch_commit` with `push: "never"` when committing from Cursor/Claude; use `--commit=conventional` for headless bulk apply after `apply` logs are green.

**See also (tracking issues):** [#11](https://github.com/Rethunk-Tech/repo-ops/issues/11) remediation apply reporting (closed) · [#12](https://github.com/Rethunk-Tech/repo-ops/issues/12) bun outdated JSON (closed) · [#13](https://github.com/Rethunk-Tech/repo-ops/issues/13) submodule umbrella workflow · [#14](https://github.com/Rethunk-Tech/repo-ops/issues/14) end-of-run roll-up (closed) · [#15](https://github.com/Rethunk-Tech/repo-ops/issues/15) optional post-apply commits (closed) · [#16](https://github.com/Rethunk-Tech/repo-ops/issues/16) plan `--json-out` artifact (closed) · [#21](https://github.com/Rethunk-Tech/repo-ops/issues/21) SHA-pinned Actions unpin (closed).
