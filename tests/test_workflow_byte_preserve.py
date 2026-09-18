"""Unit test: workflow YAML byte-preservation on pin update.

A real YAML fixture with 6-space indentation and a trailing comment is written to
disk.  ``scan_actions`` runs with ``apply=True`` and a pre-populated ``tag_cache``
(the legitimate parameter that avoids live ``gh api`` calls without mocking the
package-manager itself).  After the run exactly one line in the file must differ;
all other bytes — indentation, comments, quoting, line endings — must be
untouched.

Network: none — tag resolution is served from the in-memory ``tag_cache``.
"""

from __future__ import annotations

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
