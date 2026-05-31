"""Parse and update GitHub Actions ``uses:`` pins via ruamel.yaml (issue #19)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

# Validate parsed action@pin shape only — not used to discover pins in file text.
_PIN_TAG_RE = re.compile(r"^v[\d.]+$")
_PIN_SHA_RE = re.compile(r"^(?:sha[0-9a-f]+|[0-9a-f]{7,40})$", re.IGNORECASE)


class WorkflowParseError(Exception):
    """Workflow YAML could not be loaded or walked."""


@dataclass(frozen=True)
class UsesPin:
    workflow: str
    line: int
    col: int
    action: str
    pin: str


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


def _walk_collect(node: Any, workflow: str, out: list[UsesPin]) -> None:
    if isinstance(node, CommentedMap):
        uses_val = node.get("uses")
        if isinstance(uses_val, str):
            parsed = parse_uses_ref(uses_val)
            if parsed:
                action, pin = parsed
                line, col = _scalar_line_col(uses_val)
                out.append(
                    UsesPin(
                        workflow=workflow,
                        line=line,
                        col=col,
                        action=action,
                        pin=pin,
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
    return yaml


def parse_workflow_uses(path: Path, *, workflow_rel: str | None = None) -> list[UsesPin]:
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


def _walk_apply(
    node: Any,
    replacements: list[PinReplacement],
) -> bool:
    changed = False
    if isinstance(node, (CommentedMap, dict)):
        uses_val = node.get("uses")
        if isinstance(uses_val, str):
            parsed = parse_uses_ref(uses_val)
            if parsed:
                action, pin = parsed
                line, _ = _scalar_line_col(uses_val)
                for repl in replacements:
                    if action != repl.action or pin != repl.pin:
                        continue
                    if repl.line is not None and line != repl.line:
                        continue
                    node["uses"] = f"{repl.action}@{repl.latest}"
                    changed = True
                    break
        for child in node.values():
            if _walk_apply(child, replacements):
                changed = True
    elif isinstance(node, (CommentedSeq, list)):
        for item in node:
            if _walk_apply(item, replacements):
                changed = True
    return changed


def update_workflow_pins(path: Path, replacements: list[PinReplacement]) -> bool:
    """Apply pin updates in place and write the workflow when anything changed."""
    if not replacements:
        return False
    yaml = _yaml_loader()
    try:
        data = yaml.load(path)
    except YAMLError as exc:
        raise WorkflowParseError(f"{path.name}: {exc}") from exc
    if data is None:
        return False
    if not _walk_apply(data, replacements):
        return False
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.dump(data, fh)
    return True
