"""Integration test: cargo update --dry-run via check_rust.

Constructs a minimal Cargo project with a pinned dependency and runs the real
``check_rust`` helper (``cargo update --dry-run``).  This is a resilience test:
the workspace must not crash and must return a list of Change objects.

Network: ``cargo generate-lockfile`` may fetch crates.io on first run; skipped
when the command fails with a network-style error.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration
