"""Parse and update GitHub Actions ``uses:`` pins (issue #19).

Pin rewrites use byte-preserving targeted line edits (issue #117): only the
``@old_pin`` portion of a ``uses:`` line is substituted; all other bytes
(indentation, comments, quoting, line endings) are left untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

# Validate parsed action@pin shape only — not used to discover pins in file text.
_PIN_TAG_RE = re.compile(r"^v[\d.]+$")
_PIN_SHA_RE = re.compile(r"^(?:sha[0-9a-f]+|[0-9a-f]{7,40})$", re.IGNORECASE)

# Trailing version hint comment: "# v4" or "# v4.1.2"
_COMMENT_HINT_RE = re.compile(r"#\s*(v\d[\d.]*)")


class WorkflowParseError(Exception):
    """Workflow YAML could not be loaded or walked."""


@dataclass(frozen=True)
class UsesPin:
    workflow: str
    line: int
    col: int
    action: str
    pin: str
    comment: str | None = field(default=None)  # trailing "# vN" hint, stripped


def parse_uses_ref(value: str) -> tuple[str, str] | None:
    """Split a ``uses`` scalar into ``(action, pin)`` when it looks like action@ref."""
    text = value.strip()
    if not text or "@" not in text or " " in text:
        return None
    action, pin = text.rsplit("@", 1)
    if not action or not pin or " " in action:
        return None
    if _PIN_TAG_RE.fullmatch(pin) or _PIN_SHA_RE.fullmatch(pin):
        return action, pin
    # Reusable workflows and other refs (branches, semver without leading v).
    if re.fullmatch(r"[^\s@]+", pin):
        return action, pin
    return None


def _scalar_line_col(scalar: Any) -> tuple[int, int]:
    lc = getattr(scalar, "lc", None)
    if lc is None:
        return (0, 0)
    return (lc.line + 1, lc.col + 1)


def _uses_line_from_parent(node: CommentedMap) -> int:
    """Return the 1-based line number of the ``uses`` key in *node*.

    ruamel.yaml stores per-key position in ``node.lc.data`` as
    ``{key: [key_line, key_col, value_line, value_col]}`` (0-based).
    Plain string scalars carry no ``lc`` attribute of their own, so we
    read from the parent map instead.  Returns 0 when unavailable.
    """
    lc = getattr(node, "lc", None)
    if lc is None:
        return 0
    entry = getattr(lc, "data", {}).get("uses")
    if entry and len(entry) >= 1:
        return int(entry[0]) + 1  # convert 0-based → 1-based
    return 0


def _extract_uses_comment(node: CommentedMap) -> str | None:
    """Return the stripped trailing comment on the ``uses`` key (e.g. '# v4'), or None."""
    ca = getattr(node, "ca", None)
    if ca is None:
        return None
    entry = ca.items.get("uses")
    # ruamel stores EOL comments in slot index 2 of the [pre, post, eol, post2] list.
    if entry and len(entry) > 2 and entry[2] is not None:
        return str(entry[2].value).strip()
    return None


def _walk_collect(node: Any, workflow: str, out: list[UsesPin]) -> None:
    if isinstance(node, CommentedMap):
        uses_val = node.get("uses")
        if isinstance(uses_val, str):
            parsed = parse_uses_ref(uses_val)
            if parsed:
                action, pin = parsed
                # Prefer reading line from the parent map's lc.data (the scalar
                # value itself is a plain str with no lc attribute).
                line = _uses_line_from_parent(node)
                col = 0
                if line == 0:
                    line, col = _scalar_line_col(uses_val)
                raw_comment = _extract_uses_comment(node)
                comment: str | None = None
                if raw_comment:
                    m = _COMMENT_HINT_RE.search(raw_comment)
                    if m:
                        comment = m.group(1)  # e.g. "v4" or "v4.1.2"
                out.append(
                    UsesPin(
                        workflow=workflow,
                        line=line,
                        col=col,
                        action=action,
                        pin=pin,
                        comment=comment,
                    )
                )
        for child in node.values():
            _walk_collect(child, workflow, out)
    elif isinstance(node, CommentedSeq):
        for item in node:
            _walk_collect(item, workflow, out)
    elif isinstance(node, dict):
        uses_val = node.get("uses")
        if isinstance(uses_val, str):
            parsed = parse_uses_ref(uses_val)
            if parsed:
                action, pin = parsed
                out.append(
                    UsesPin(
                        workflow=workflow,
                        line=0,
                        col=0,
                        action=action,
                        pin=pin,
                    )
                )
        for child in node.values():
            _walk_collect(child, workflow, out)
    elif isinstance(node, list):
        for item in node:
            _walk_collect(item, workflow, out)


def _yaml_loader() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    # GitHub Actions workflows conventionally indent block-sequence dashes
    # two spaces past their parent mapping key (e.g. `steps:` at 4 spaces,
    # `- uses:` at 6). ruamel's round-trip mode preserves most formatting on
    # a no-op load+dump, but sequence indent is a dump-time setting, not
    # inferred from the loaded document — without this, workflow_roundtrip_text
    # silently reformats every block sequence to ruamel's own default
    # (offset=0) instead of leaving it untouched.
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def workflow_roundtrip_text(path: Path) -> str:
    """Load and dump a workflow file (for tests and no-op drift checks)."""
    yaml = _yaml_loader()
    data = yaml.load(path)
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


def parse_workflow_uses(
    path: Path, *, workflow_rel: str | None = None
) -> list[UsesPin]:
    """Load workflow YAML and collect every ``uses:`` scalar (steps, jobs, etc.)."""
    rel = workflow_rel if workflow_rel is not None else path.name
    try:
        data = _yaml_loader().load(path)
    except YAMLError as exc:
        raise WorkflowParseError(f"{rel}: {exc}") from exc
    except OSError as exc:
        raise WorkflowParseError(f"{rel}: {exc}") from exc
    if data is None:
        return []
    out: list[UsesPin] = []
    _walk_collect(data, rel, out)
    return out


@dataclass(frozen=True)
class PinReplacement:
    action: str
    pin: str
    latest: str
    line: int | None = None
    # Optional comment rewrite for cross-major SHA refresh (#138): when set,
    # the first element is the old comment text and the second is the new comment
    # text.  Applied on the same line as the pin replacement (e.g. ``"# v6"``
    # → ``"# v7"`` when a SHA pin crosses a major boundary).
    comment_rewrite: tuple[str, str] | None = None
    # Set (replacing any existing trailing comment, or appending if none) when
    # pinning a previously-floating tag to a SHA (#NNN) — the new SHA needs a
    # ``# vN`` audit hint whether or not the original tag ref had one at all.
    # Unlike comment_rewrite, this does not require the old text to match.
    set_comment: str | None = None


def _apply_pin_to_line(line: str, action: str, old_pin: str, new_pin: str) -> str:
    """Return *line* with ``@old_pin`` replaced by ``@new_pin`` for *action*.

    Matches the ``uses:`` value on the line and replaces only the ``@<ref>``
    portion, leaving every other byte (indentation, comments, quoting, line
    endings) untouched (issue #117).

    The match is anchored to the action name and uses a negative lookahead for
    ``.`` and word characters so that a v3 → v3.19.0 bump does not
    accidentally corrupt an existing ``v3.19.0`` pin on the same line.
    """
    escaped_action = re.escape(action)
    escaped_pin = re.escape(old_pin)
    # After the pin must come a non-word / non-dot character (whitespace, #,
    # newline) or end-of-string — prevents partial match of "v3" inside "v3.19.0".
    pattern = re.compile(
        r"(?<=" + escaped_action + r"@)" + escaped_pin + r"(?=[^\w.]|\Z)"
    )
    new_line, count = pattern.subn(new_pin, line)
    if count:
        return new_line
    return line


def _apply_comment_rewrite_to_line(
    line: str, old_comment: str, new_comment: str
) -> str:
    """Return *line* with the first occurrence of *old_comment* replaced by *new_comment*.

    Used for cross-major SHA refreshes (#138): rewrites the trailing ``# vN``
    audit hint when the pin advances to a different major.  Only replaces the
    exact string (word-boundary anchored so ``# v6`` does not clobber
    ``# v6.0.3``).
    """
    escaped = re.escape(old_comment)
    # The comment must be followed by a non-digit character or end-of-string so
    # "# v6" does not partially match "# v6.0.3".
    pattern = re.compile(escaped + r"(?=\D|\Z)")
    new_line, count = pattern.subn(new_comment, line, count=1)
    if count:
        return new_line
    return line


def _apply_comment_to_line(line: str, new_comment: str) -> str:
    """Return *line* with its trailing comment set to *new_comment*.

    Replaces any existing trailing ``# ...`` comment verbatim, or appends one
    (with a leading two-space gap, matching this repo's style) if the line
    has none. Used when pinning a floating tag to SHA — the new SHA needs an
    audit-hint comment whether or not the original tag ref had one.
    """
    ending = ""
    body = line
    for eol in ("\r\n", "\n"):
        if line.endswith(eol):
            ending = eol
            body = line[: -len(eol)]
            break
    hash_idx = body.find("#")
    prefix = body[:hash_idx] if hash_idx != -1 else body
    return prefix.rstrip() + "  " + new_comment + ending


def update_workflow_pins(path: Path, replacements: list[PinReplacement]) -> bool:
    """Apply pin updates in place using byte-preserving targeted line edits.

    Only the ``@old_pin`` portion of each matching ``uses:`` line is
    substituted; all other bytes (indentation, comments, quoting, line
    endings) are left untouched (issue #117).

    When a ``PinReplacement`` carries a ``line`` number, only that line is
    edited.  When ``line`` is ``None``, every line containing the action@pin
    is updated (backwards-compatible fallback for callers that do not supply
    line numbers).

    When ``comment_rewrite`` is set, the old comment text is also replaced by
    the new comment text on the same line after the pin replacement — used for
    cross-major SHA refreshes (#138) to keep the ``# vN`` audit hint accurate.
    """
    if not replacements:
        return False
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowParseError(f"{path.name}: {exc}") from exc

    lines = raw.splitlines(keepends=True)
    changed = False

    for repl in replacements:
        action = repl.action
        old_pin = repl.pin
        new_pin = repl.latest
        if old_pin == new_pin:
            continue

        if repl.line is not None:
            # Targeted single-line edit (preferred path).
            idx = repl.line - 1  # 1-based → 0-based
            if 0 <= idx < len(lines):
                new_line = _apply_pin_to_line(lines[idx], action, old_pin, new_pin)
                if repl.comment_rewrite is not None:
                    old_cmt, new_cmt = repl.comment_rewrite
                    new_line = _apply_comment_rewrite_to_line(
                        new_line, old_cmt, new_cmt
                    )
                if repl.set_comment is not None:
                    new_line = _apply_comment_to_line(new_line, repl.set_comment)
                if new_line != lines[idx]:
                    lines[idx] = new_line
                    changed = True
        else:
            # No line hint — scan all lines for action@pin.
            for idx, line in enumerate(lines):
                new_line = _apply_pin_to_line(line, action, old_pin, new_pin)
                if repl.comment_rewrite is not None:
                    old_cmt, new_cmt = repl.comment_rewrite
                    new_line = _apply_comment_rewrite_to_line(
                        new_line, old_cmt, new_cmt
                    )
                if repl.set_comment is not None and new_line != line:
                    # Only when the pin substitution actually matched this
                    # line — set_comment is not content-scoped like
                    # comment_rewrite, so applying it unconditionally in this
                    # scan-all-lines fallback would corrupt unrelated lines.
                    new_line = _apply_comment_to_line(new_line, repl.set_comment)
                if new_line != line:
                    lines[idx] = new_line
                    changed = True

    if not changed:
        return False
    try:
        path.write_text("".join(lines), encoding="utf-8")
    except OSError as exc:
        raise WorkflowParseError(f"{path.name}: {exc}") from exc
    return True
