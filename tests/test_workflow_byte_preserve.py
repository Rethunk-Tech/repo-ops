"""Unit test: workflow YAML byte-preservation on pin update.

A real YAML fixture with 6-space indentation and a trailing comment is written to
disk.  ``scan_actions`` runs with ``apply=True`` and a pre-populated ``tag_cache``
(the legitimate parameter that avoids live ``gh api`` calls without mocking the
package-manager itself).  After the run exactly one line in the file must differ;
all other bytes — indentation, comments, quoting, line endings — must be
untouched.

Network: none — tag resolution is served from the in-memory ``tag_cache``.
"""
