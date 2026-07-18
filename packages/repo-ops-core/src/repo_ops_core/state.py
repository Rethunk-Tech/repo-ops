"""Persistent run-state store for repo-ops.

Stores a small JSON document at ``$XDG_CACHE_HOME/repo-ops/state.json``
(default ``~/.cache/repo-ops/state.json``) using atomic tmp+rename writes.

Schema (version 1)::

    {
      "version": 1,
      "dirty_skips": {
        "<abs repo path>": {"first_seen": "YYYY-MM-DD", "runs": N}
      },
      "behind_latest": {
        "<abs repo path>": {
          "first_behind": "YYYY-MM-DD",
          "prev_total": N,
          "by_manager": {"<manager>": N},
          "runs": N
        }
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
import threading
from datetime import date
from pathlib import Path
from typing import Any

# Module-level lazy state cache.  None = not yet loaded.
_CACHE: dict[str, Any] | None = None
_DIRTY: bool = False  # True once any write has been made to _CACHE
_LOCK: threading.Lock = threading.Lock()

#: Schema version stamped into state.json on every write.
STATE_SCHEMA_VERSION = 1


def _stamp_version(state: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *state* with ``version`` set for disk writes."""
    stamped = dict(state)
    stamped["version"] = STATE_SCHEMA_VERSION
    return stamped


def _state_path() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "repo-ops" / "state.json"


def _no_state() -> bool:
    return os.environ.get("REPO_OPS_NO_STATE") == "1"


def _ensure_loaded() -> dict[str, Any]:
    """Return the module-level cache, loading from disk on first call.

    Uses a double-checked locking pattern so concurrent threads in
    run.py's bounded pool only pay the lock cost on the very first call.
    """
    global _CACHE
    if _CACHE is None:
        with _LOCK:
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
        text = json.dumps(_stamp_version(state), indent=2) + "\n"
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
    with _LOCK:
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
    with _LOCK:
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


# ---------------------------------------------------------------------------
# Behind-latest staleness tracking
# ---------------------------------------------------------------------------
#
# Mirrors the dirty-skip pattern: orchestration records per-workspace
# behind-latest counts as workspaces complete; report enrichment reads the
# per-repo roll-up back out.  Only what scoring needs is kept — the date a
# repo was first seen behind (``first_behind``), the last run's per-manager
# behind counts (``by_manager``), the previous run's total (``prev_total``,
# for the since-last-run delta), and a run counter — never unbounded history.
#
# Entries are only created for repos that actually report packages behind
# latest, and a repo whose last run reported zero behind has its
# ``first_behind`` reset (popped) at the next run's rotation, so a repo that
# catches up and later falls behind again ages from the new date.

# Repos whose behind-latest entry has been rotated this process ("this run").
# The first workspace of a repo in a run rotates the previous run's totals
# into ``prev_total``; subsequent workspaces of the same repo accumulate.
_BEHIND_RUN_SEEN: set[str] = set()


def record_behind_latest(
    state: dict[str, Any],
    repo_path: str | Path,
    manager: str,
    count: int,
    today: date,
    *,
    new_run: bool,
) -> dict[str, Any] | None:
    """Record a workspace's behind-latest *count* for *repo_path* in *state*.

    ``new_run=True`` rotates the entry first: the previous run's total moves
    to ``prev_total``, per-manager counts reset, the run counter increments,
    and ``first_behind`` is popped when the previous run reported 0 behind.
    A rotated entry left with nothing behind, nothing previously behind and
    no first-behind date is pruned entirely.

    Never creates an entry for a repo with nothing behind.  Returns the
    surviving entry, or ``None`` when pruned, absent, or state is disabled.
    """
    if _no_state():
        return None
    key = str(repo_path)
    table: dict[str, Any] = state.setdefault("behind_latest", {})
    entry = table.get(key)
    if entry is None:
        if count <= 0:
            return None
        entry = {"prev_total": 0, "by_manager": {}, "runs": 0}
        table[key] = entry
        new_run = True
    if new_run:
        old: dict[str, Any] = entry.get("by_manager") or {}
        old_total = sum(v for v in old.values() if isinstance(v, int))
        if old_total == 0:
            # Previous run reported nothing behind — restart the aging clock.
            entry.pop("first_behind", None)
        entry["prev_total"] = old_total
        entry["by_manager"] = {}
        entry["runs"] = entry.get("runs", 0) + 1
        if count <= 0 and old_total == 0 and "first_behind" not in entry:
            # Fully caught up for two runs — nothing left worth scoring.
            del table[key]
            return None
    if count > 0:
        by_manager: dict[str, Any] = entry["by_manager"]
        by_manager[manager] = by_manager.get(manager, 0) + count
        entry.setdefault("first_behind", today.isoformat())
    return entry


def cache_record_behind_latest(
    repo_path: str | Path,
    manager: str,
    count: int,
    today: date,
) -> None:
    """Record a workspace's behind-latest count against the module-level cache.

    The first call for a repo in this process rotates the previous run's
    totals; later calls (other workspaces of the same repo) accumulate.
    No-op — and leaves the store untouched — when the repo has no existing
    entry and *count* is 0, so clean repos never bloat the state file.
    """
    global _DIRTY
    if _no_state():
        return
    state = _ensure_loaded()
    key = str(repo_path)
    with _LOCK:
        existing = state.get("behind_latest") or {}
        if key not in existing and count <= 0:
            return
        new_run = key not in _BEHIND_RUN_SEEN
        entry = record_behind_latest(
            state, repo_path, manager, count, today, new_run=new_run
        )
        if entry is not None:
            _BEHIND_RUN_SEEN.add(key)
        _DIRTY = True


def cache_get_staleness(repo_path: str | Path, today: date) -> dict[str, Any] | None:
    """Return computed staleness for *repo_path*, or ``None`` when untracked.

    Read-only.  The result dict carries:

    - ``total_behind`` — last recorded run's total behind-latest count
    - ``by_manager`` — that total split per package manager
    - ``first_behind`` — ISO date the repo was first seen behind (``None``
      when nothing is currently behind)
    - ``runs`` — number of runs recorded for the repo
    - ``staleness_score`` — ``total_behind * days_since_first_behind``,
      rounded to 1 decimal; ``0.0`` when nothing is behind
    - ``new_behind_since_last_run`` — ``max(0, total - previous run total)``
    """
    if _no_state():
        return None
    state = _ensure_loaded()
    with _LOCK:
        table = state.get("behind_latest") or {}
        entry = table.get(str(repo_path))
        if not isinstance(entry, dict):
            return None
        by_manager = {
            str(k): v
            for k, v in (entry.get("by_manager") or {}).items()
            if isinstance(v, int)
        }
        total = sum(by_manager.values())
        first_behind = entry.get("first_behind") if total > 0 else None
        score = 0.0
        if total > 0 and isinstance(first_behind, str):
            try:
                days = max(0, (today - date.fromisoformat(first_behind)).days)
            except ValueError:
                days = 0
            score = round(float(total * days), 1)
        prev = entry.get("prev_total")
        prev_total = prev if isinstance(prev, int) else 0
        runs = entry.get("runs")
        return {
            "total_behind": total,
            "by_manager": by_manager,
            "first_behind": first_behind if isinstance(first_behind, str) else None,
            "runs": runs if isinstance(runs, int) else 0,
            "staleness_score": score,
            "new_behind_since_last_run": max(0, total - prev_total),
        }


# ---------------------------------------------------------------------------
# Apply-checkpoint (REPO_OPS_RESUME)
# ---------------------------------------------------------------------------
#
# Stores {<abs repo path>: <post-apply HEAD SHA>} in a separate file so that
# a large-fleet apply run can be resumed after a crash.  Each repo whose
# apply completed without failure is checkpointed immediately (atomic write).
# On re-run with REPO_OPS_RESUME=1, repos whose current HEAD matches the
# checkpointed post-apply SHA are skipped — they are already done.
#
# The checkpoint lives alongside state.json:
#   ~/.cache/repo-ops/apply-checkpoint.json
#
# Like state.json, the checkpoint respects REPO_OPS_NO_STATE=1 (returns
# empty dict / writes nothing when disabled).

_CHECKPOINT_LOCK: threading.Lock = threading.Lock()


def _checkpoint_path() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "repo-ops" / "apply-checkpoint.json"


def load_checkpoint() -> dict[str, str]:
    """Load the apply checkpoint from disk.

    Returns a ``{repo_path: post_apply_sha}`` dict, or an empty dict when
    ``REPO_OPS_NO_STATE=1`` or the file is missing / corrupt.
    """
    if _no_state():
        return {}
    path = _checkpoint_path()
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        return {}
    except Exception:
        return {}


def save_checkpoint(data: dict[str, str]) -> None:
    """Persist *data* to the apply-checkpoint file atomically (tmp + rename).

    No-op when ``REPO_OPS_NO_STATE=1`` or on any I/O error (best-effort).
    Callers are responsible for holding ``_CHECKPOINT_LOCK`` while building
    the dict snapshot passed here; the write itself does not need the lock
    because ``os.replace`` is atomic on all POSIX systems.
    """
    if _no_state():
        return
    path = _checkpoint_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2) + "\n"
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
