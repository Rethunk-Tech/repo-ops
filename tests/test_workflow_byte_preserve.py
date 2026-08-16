"""Unit test: workflow YAML byte-preservation on pin update (issue #117).

A real YAML fixture with 6-space indentation and a trailing comment is written to
disk.  ``scan_actions`` runs with ``apply=True`` and a pre-populated ``tag_cache``
(the legitimate parameter that avoids live ``gh api`` calls without mocking the
package-manager itself).  After the run exactly one line in the file must differ;
all other bytes — indentation, comments, quoting, line endings — must be
untouched.

Network: none — tag resolution is served from the in-memory ``tag_cache``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from repo_ops_upgrade.actions import scan_actions

# A workflow with non-standard 6-space indentation and a trailing comment.
# Only ``astral-sh/setup-uv@v5.4.0`` has a newer in-major exact tag (v5.5.0)
# and will be rewritten.  ``actions/checkout@v3`` is already at its floating
# major, so in normalize mode it is reported only (newer major v4 available)
# and NOT rewritten.  This guarantees exactly one changed line.
_FIXTURE_YAML = """\
name: Test
on: push

jobs:
      build:
            runs-on: ubuntu-latest
            steps:
            - name: Checkout
              uses: actions/checkout@v3  # important comment preserved
            - name: Setup Python
              uses: astral-sh/setup-uv@v5.4.0
"""

# Pre-populated cache replaces the live ``gh api`` call.
# checkout: bare major v3 and v4 exist (floating), max_major=4.
# setup-uv: no bare major tags (exact-only publisher), v5.5.0 is newest.
_TAG_CACHE: dict[str, dict | None] = {
    "actions/checkout": {
        "bare_majors": {3, 4},
        "max_major": 4,
        "all_tags": {"v3", "v4", "v4.1.0"},
        "sha_to_tags": {},
    },
    "astral-sh/setup-uv": {
        "bare_majors": set(),
        "max_major": 5,
        "all_tags": {"v5.4.0", "v5.5.0"},
        "sha_to_tags": {},
    },
}


def test_workflow_pin_update_changes_exactly_one_line() -> None:
    """update_workflow_pins rewrites only the pin; all other bytes are preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        wf = wf_dir / "ci.yml"
        wf.write_text(_FIXTURE_YAML, encoding="utf-8")

        before_lines = _FIXTURE_YAML.splitlines(keepends=True)

        _bumps, n_workflows = scan_actions(
            repo,
            apply=True,
            tag_cache=dict(_TAG_CACHE),  # fresh copy; scan_actions mutates it
            lint_level="off",
        )

        after_text = wf.read_text(encoding="utf-8")
        after_lines = after_text.splitlines(keepends=True)

        assert n_workflows == 1, "exactly one workflow file found"
        assert len(before_lines) == len(after_lines), "no lines inserted or deleted"

        changed = [
            (i + 1, b, a)
            for i, (b, a) in enumerate(zip(before_lines, after_lines, strict=False))
            if b != a
        ]

        assert len(changed) == 1, f"expected exactly 1 changed line, got {len(changed)}: {changed}"

        line_no, before_line, after_line = changed[0]
        # The rewritten line must contain the new pin and nothing else changed.
        assert "astral-sh/setup-uv@v5.5.0" in after_line, (
            f"expected setup-uv pin bumped on line {line_no}: {after_line!r}"
        )
        # Indentation is preserved: same leading whitespace.
        assert (
            before_line[: len(before_line) - len(before_line.lstrip())]
            == (after_line[: len(after_line) - len(after_line.lstrip())])
        ), "indentation changed"
        # The trailing comment on checkout line must be unchanged.
        checkout_line = next(line for line in after_lines if "actions/checkout" in line)
        assert "# important comment preserved" in checkout_line
