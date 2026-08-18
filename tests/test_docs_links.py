"""Internal Markdown links must resolve — both the file and the anchor.

Renaming a heading silently breaks every link pointing at its old anchor, and
nothing in the build notices. This walks the repo's own Markdown and checks each
relative link resolves to a real file, and each `#fragment` to a real heading.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LINK = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(?P<text>.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"```.*?```", re.DOTALL)

#: Directories that are scratch or vendored rather than repository documentation.
SKIP_DIRS = {".git", ".venv", "node_modules", ".orchestrate", "dist", ".mypy_cache"}

#: The bundled skill ships standalone; `install-skill` rewrites its relative
#: links to github.com URLs at copy time, so they are not expected to resolve
#: in-tree. test_install_skill covers that rewrite.
SKIP_FILES = {Path("src/repo_ops/skills/repo-ops/REFERENCE.md")}


def _markdown_files() -> list[Path]:
    return sorted(
        p
        for p in _ROOT.rglob("*.md")
        if not SKIP_DIRS & set(p.relative_to(_ROOT).parts)
        and p.relative_to(_ROOT) not in SKIP_FILES
    )


def _anchors(text: str) -> set[str]:
    """GitHub slugs: lowercase, punctuation dropped, spaces to hyphens."""
    slugs = set()
    for match in _HEADING.finditer(text):
        slug = match["text"].lower()
        slug = re.sub(r"[^\w\s-]", "", slug.replace("`", ""))
        # Each space becomes its own hyphen: dropping an em-dash leaves two
        # spaces, and GitHub's slug keeps both as "--".
        slugs.add(re.sub(r"\s", "-", slug.strip()))
    return slugs


def test_internal_links_and_anchors_resolve() -> None:
    broken: list[str] = []
    for path in _markdown_files():
        body = _FENCE.sub("", path.read_text(encoding="utf-8"))
        rel = path.relative_to(_ROOT)
        for match in _LINK.finditer(body):
            target = match["target"]
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, fragment = target.partition("#")
            resolved = path.parent / file_part if file_part else path
            if not resolved.exists():
                broken.append(f"{rel}: missing file {target}")
                continue
            if fragment and fragment not in _anchors(resolved.read_text(encoding="utf-8")):
                broken.append(f"{rel}: missing anchor {target}")

    assert not broken, "broken internal links:\n  " + "\n  ".join(broken)
