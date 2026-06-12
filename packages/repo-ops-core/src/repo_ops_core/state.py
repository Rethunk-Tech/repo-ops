"""Persistent run-state store for repo-ops.

Stores a small JSON document at ``$XDG_CACHE_HOME/repo-ops/state.json``
(default ``~/.cache/repo-ops/state.json``) using atomic tmp+rename writes.

Schema (version 1)::

    {
      "dirty_skips": {
        "<abs repo path>": {"first_seen": "YYYY-MM-DD", "runs": N}
      }
    }

All public helpers silently become no-ops (returning ``None``) when the
environment variable ``REPO_OPS_NO_STATE=1`` is set.

A module-level lazy cache (``_CACHE``) is populated on first access and
flushed to disk via an ``atexit`` handler registered on first write, so
callers that cannot pass a state dict around (e.g. workers in run.py)
get persistence automatically.
"""

from __future__ import annotations

import atexit
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

# Module-level lazy state cache.  None = not yet loaded.
_CACHE: dict[str, Any] | None = None
_DIRTY: bool = False  # True once any write has been made to _CACHE


def _state_path() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "repo-ops" / "state.json"


def _no_state() -> bool:
    return os.environ.get("REPO_OPS_NO_STATE") == "1"


def _ensure_loaded() -> dict[str, Any]:
    """Return the module-level cache, loading from disk on first call."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _read_from_disk()
    return _CACHE


def _read_from_disk() -> dict[str, Any]:
    """Read state.json from disk.  Returns empty dict on any error."""
    path = _state_path()
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _flush_cache() -> None:
    """atexit hook: persist the in-memory cache to disk if it was modified."""
    if _DIRTY and _CACHE is not None and not _no_state():
        _write_to_disk(_CACHE)


atexit.register(_flush_cache)


def _write_to_disk(state: dict[str, Any]) -> None:
    """Atomic tmp+rename write.  Silently swallows all errors."""
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(state, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    except Exception:
        return  # best-effort; disk write failure must not crash the run


# ---------------------------------------------------------------------------
# Public API: explicit state-dict form (for callers that own the dict)
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    """Load state from disk.  Returns an empty dict on any error or missing file."""
    if _no_state():
        return {}
    return _read_from_disk()


def save_state(state: dict[str, Any]) -> None:
    """Persist *state* to disk atomically (tmp + rename).  No-op on error."""
    if _no_state():
        return
    _write_to_disk(state)


def record_dirty_skip(
    state: dict[str, Any],
    repo_path: str | Path,
    today: date,
) -> date | None:
    """Record a dirty-skip event for *repo_path* in *state*.

    Increments the run counter and sets ``first_seen`` on the first call.
    Returns the ``first_seen`` date.  Returns ``None`` when ``REPO_OPS_NO_STATE=1``.
    """
    if _no_state():
        return None
    key = str(repo_path)
    dirty_skips: dict[str, Any] = state.setdefault("dirty_skips", {})
    entry = dirty_skips.get(key)
    if entry is None:
        entry = {"first_seen": today.isoformat(), "runs": 0}
        dirty_skips[key] = entry
    entry["runs"] = entry.get("runs", 0) + 1
    return date.fromisoformat(entry["first_seen"])


def clear_dirty_skip(
    state: dict[str, Any],
    repo_path: str | Path,
) -> None:
    """Remove any dirty-skip tracking entry for *repo_path* from *state*.

    No-op when ``REPO_OPS_NO_STATE=1`` or entry does not exist.
    """
    if _no_state():
        return
    dirty_skips = state.get("dirty_skips", {})
    dirty_skips.pop(str(repo_path), None)


# ---------------------------------------------------------------------------
# Module-cache form: used by orchestration workers that share no dict
# ---------------------------------------------------------------------------


def cache_record_dirty_skip(repo_path: str | Path, today: date) -> date | None:
    """Record a dirty-skip against the module-level cache.

    Returns ``first_seen`` date or ``None`` when disabled.
    The cache is flushed to disk at process exit via ``atexit``.
    """
    global _DIRTY
    if _no_state():
        return None
    state = _ensure_loaded()
    first_seen = record_dirty_skip(state, repo_path, today)
    _DIRTY = True
    return first_seen


def cache_clear_dirty_skip(repo_path: str | Path) -> None:
    """Remove any dirty-skip entry for *repo_path* from the module-level cache."""
    global _DIRTY
    if _no_state():
        return
    state = _ensure_loaded()
    key = str(repo_path)
    dirty_skips = state.get("dirty_skips", {})
    if key in dirty_skips:
        dirty_skips.pop(key)
        _DIRTY = True


def cache_get_dirty_skip(repo_path: str | Path) -> dict[str, Any] | None:
    """Return the raw dirty-skip entry for *repo_path*, or ``None``."""
    if _no_state():
        return None
    state = _ensure_loaded()
    dirty_skips: dict[str, Any] = state.get("dirty_skips") or {}
    entry = dirty_skips.get(str(repo_path))
    if isinstance(entry, dict):
        return entry
    return None
