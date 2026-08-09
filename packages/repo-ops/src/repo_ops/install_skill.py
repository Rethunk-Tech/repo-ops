"""Install bundled repo-ops agent skill into Cursor and Claude Code homes."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

import repo_ops

_SKILL_DIR_NAME = "repo-ops"
_TARGETS = ("cursor", "claude", "both")
_GITHUB_BLOB_BASE = "https://github.com/Rethunk-Tech/repo-ops/blob/main/"
_INSTALL_LINK_RE = re.compile(r"\]\(((?:HUMANS\.md|docs/|packages/)[^)]*)\)")


def bundled_skill_dir() -> Path:
    root = Path(repo_ops.__file__).resolve().parent
    skill = root / "skills" / _SKILL_DIR_NAME
    if not skill.is_dir():
        msg = f"bundled skill not found at {skill}"
        raise FileNotFoundError(msg)
    return skill


def resolve_home(home: Path | None) -> Path:
    if home is not None:
        return home.expanduser().resolve()
    env = os.environ.get("REPO_OPS_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home()


def destination_for(target: str, home: Path) -> Path:
    if target == "cursor":
        return home / ".cursor" / "skills" / _SKILL_DIR_NAME
    if target == "claude":
        return home / ".claude" / "skills" / _SKILL_DIR_NAME
    msg = f"unknown target: {target}"
    raise ValueError(msg)


def destinations_for(target: str, home: Path) -> list[tuple[str, Path]]:
    if target == "both":
        return [
            ("cursor", destination_for("cursor", home)),
            ("claude", destination_for("claude", home)),
        ]
    return [(target, destination_for(target, home))]


def rewrite_install_links(text: str) -> str:
    """Rewrite relative HUMANS/docs/packages links to canonical GitHub blob URLs."""

    def _replace(match: re.Match[str]) -> str:
        rel = match.group(1)
        return f"]({_GITHUB_BLOB_BASE}{rel})"

    return _INSTALL_LINK_RE.sub(_replace, text)


def _skill_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        if path.suffix == ".md":
            content = rewrite_install_links(content.decode("utf-8")).encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def check_installed_skill(src: Path, dest: Path) -> bool:
    """Return whether an installed skill matches the bundled skill."""
    if not dest.exists():
        return True
    return (
        dest.is_dir()
        and not dest.is_symlink()
        and _skill_digest(src) == _skill_digest(dest)
    )


def _rewrite_markdown_links(dest: Path) -> None:
    for md in dest.rglob("*.md"):
        original = md.read_text(encoding="utf-8")
        rewritten = rewrite_install_links(original)
        if rewritten != original:
            md.write_text(rewritten, encoding="utf-8")


def install_one(
    src: Path,
    dest: Path,
    *,
    force: bool,
    dry_run: bool,
) -> int:
    if dest.exists():
        if not force:
            print(f"skip {dest} (exists; use --force to overwrite)", file=sys.stderr)
            # A dry run only simulates; an existing target is not a failure.
            return 0 if dry_run else 1
        if dry_run:
            print(f"would remove {dest}")
        else:
            shutil.rmtree(dest)

    if dry_run:
        print(f"would install {src} -> {dest}")
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    _rewrite_markdown_links(dest)
    print(f"installed {dest}")
    return 0


_INSTALL_SKILL_DESCRIPTION = (
    "Install the bundled repo-ops agent skill for Cursor and/or Claude Code."
)


def _add_install_skill_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--target",
        choices=_TARGETS,
        default="both",
        help="where to install (default: both)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check installed skill copies for drift without writing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing skill directories",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print planned installs without writing files",
    )
    parser.add_argument(
        "--home",
        type=Path,
        metavar="DIR",
        help="override home directory (also REPO_OPS_HOME env)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        metavar="DIR",
        help="install directly to this skill directory (testing; ignores --target layout)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops install-skill",
        description=_INSTALL_SKILL_DESCRIPTION,
    )
    _add_install_skill_arguments(parser)
    return parser


def validate_dest(dest: Path, src: Path) -> None:
    """Refuse paths that are not the repo-ops skill directory.

    Also refuses when *dest* is equal to, an ancestor of, or a descendant
    of *src* — any such overlap would cause ``shutil.rmtree(dest)`` to
    destroy the source checkout.
    """
    resolved = dest.expanduser().resolve()
    src_resolved = src.resolve()

    if resolved.name != _SKILL_DIR_NAME:
        msg = (
            f"--dest must end with {_SKILL_DIR_NAME!r} (got {resolved.name!r}); "
            "refusing to remove an unrelated directory"
        )
        raise ValueError(msg)

    # Guard: dest == src, dest is ancestor of src, or src is ancestor of dest.
    # Path.parents is component-aware so /foo/bar-extra does not match /foo/bar.
    if (
        resolved == src_resolved
        or resolved in src_resolved.parents
        or src_resolved in resolved.parents
    ):
        msg = (
            f"--dest {resolved} overlaps with the source skill directory "
            f"{src_resolved}; refusing to remove source checkout"
        )
        raise ValueError(msg)


def run_install(
    *,
    target: str,
    force: bool,
    dry_run: bool,
    home: Path | None,
    dest: Path | None,
    check: bool = False,
) -> int:
    src = bundled_skill_dir()
    if check:
        if dest is not None:
            try:
                validate_dest(dest, src)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            destinations = [("dest", dest.expanduser().resolve())]
        else:
            destinations = destinations_for(target, resolve_home(home))

        codes: list[int] = []
        for label, path in destinations:
            if not path.exists():
                print(f"not installed {label}: {path}")
                codes.append(0)
            elif check_installed_skill(src, path):
                print(f"ok {label}: {path}")
                codes.append(0)
            else:
                print(
                    f"warning: installed skill drift at {path}; "
                    "run with --force to overwrite",
                    file=sys.stderr,
                )
                codes.append(1)
        return 1 if any(codes) else 0

    if dest is not None:
        try:
            validate_dest(dest, src)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        dest_resolved = dest.expanduser().resolve()
        code = install_one(src, dest_resolved, force=force, dry_run=dry_run)
        return code

    home_resolved = resolve_home(home)
    codes: list[int] = []
    for label, path in destinations_for(target, home_resolved):
        code = install_one(src, path, force=force, dry_run=dry_run)
        if code != 0 and not dry_run:
            print(f"failed target {label}", file=sys.stderr)
        codes.append(code)
    return 1 if any(codes) else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_install(
        target=args.target,
        force=args.force,
        dry_run=args.dry_run,
        home=args.home,
        dest=args.dest,
        check=args.check,
    )


if __name__ == "__main__":
    sys.exit(main())
