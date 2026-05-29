#!/usr/bin/env python3
"""doc-audit — lint the README/HUMANS/AGENTS/CONTRIBUTING/spec tier split.

Enforces the three-tier doc governance rule defined in
Rethunk-Tech/Bastion/.cursor/rules/documentation-governance.mdc:

    README  = orientation + hierarchy + links
    HUMANS  = authoritative run/use (install, env, usage, verify)
    AGENTS  = LLM/dev internals (layout, invariants, state)

Plus CONTRIBUTING (commit conventions, style, test steps) and specs/
(requirements + design). Flags tier violations, stale references to
deleted files, and drift-describing framing in otherwise-current docs.

Zero external deps. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# --- Rule catalogue ---------------------------------------------------------

CATEGORIES = {
    # Tier violation rules (content in wrong tier)
    "readme-install":           "README contains install procedure (belongs in HUMANS)",
    "readme-env":               "README contains env-var table (belongs in HUMANS)",
    "readme-usage":             "README contains usage walkthrough (belongs in HUMANS)",
    "readme-uninstall":         "README contains uninstall section (belongs in HUMANS)",
    "readme-no-tier-links":     "README does not link to HUMANS / AGENTS / spec",
    "readme-missing":           "README.md missing",
    "humans-missing":           "HUMANS.md missing",
    "agents-missing":           "AGENTS.md missing",
    "agents-commit":            "AGENTS has commit-conventions section (belongs in CONTRIBUTING)",
    "agents-stale-tests":       "AGENTS claims 'no automated tests' but tests/ directory exists",
    "dup-heading":              "Same H2 heading present in README and HUMANS (duplication)",
    # 7-part README structure rules
    "readme-title-not-centered":"README H1 is plain Markdown instead of <h1 align=\"center\"> (7-part spec)",
    "readme-no-separator":      "README missing '---' separator after badge block (7-part spec)",
    "readme-no-highlights":     "README missing ## Highlights / ## Features section (7-part spec)",
    "readme-no-docs-table":     "README missing ## Documentation pipe-table (7-part spec)",
    "readme-no-license-footer": "README missing license/copyright footer (7-part spec)",
    "readme-no-badges":         "README missing centered shields.io badge block (7-part spec)",
    # Stale content rules
    "stale-ref":                "Doc references a file/tool that no longer exists in the repo",
    "spec-drift-framing":       "Spec uses 'historical note'/'was removed'/'defensive' (describes drift rather than current state)",
    "todo-current-state":       "TODO.md contains current-state rows (should be future-only)",
}

SEVERITY = {
    "FAIL": "FAIL",
    "WARN": "WARN",
    "INFO": "INFO",
}


@dataclass
class Finding:
    category: str
    severity: str
    path: str
    line: int
    message: str


@dataclass
class Report:
    root: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, category: str, severity: str, path: str, line: int, message: str) -> None:
        self.findings.append(Finding(category, severity, path, line, message))


# --- Discovery --------------------------------------------------------------


def find_repo_root(start: Path) -> Path:
    """Walk upward looking for a `.git` entry. Fall back to `start`."""
    p = start.resolve()
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    return start.resolve()


def read(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def iter_h2(lines: list[str]) -> Iterable[tuple[int, str]]:
    for i, line in enumerate(lines, 1):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            yield i, m.group(1).strip()


def iter_code_blocks(lines: list[str]) -> Iterable[tuple[int, str]]:
    """Yield (start_line, body) for each fenced code block."""
    i = 0
    while i < len(lines):
        if lines[i].startswith("```"):
            start = i + 1
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            yield start, "\n".join(body)
        i += 1


# --- README rules -----------------------------------------------------------


INSTALL_HEADINGS = re.compile(r"^(installation?|setup|getting started|quickstart|quick start)$", re.I)
USAGE_HEADINGS = re.compile(r"^(usage|how to use|commands|cli usage)$", re.I)
UNINSTALL_HEADINGS = re.compile(r"^un(install|setup)$", re.I)
ENV_HEADINGS = re.compile(r"^(environment variables?|env vars?|configuration)$", re.I)


def audit_readme(root: Path, report: Report) -> None:
    path = root / "README.md"
    if not path.exists():
        report.add("readme-missing", "FAIL", "README.md", 0, "README.md not found")
        return
    lines = read(path)

    # Tier heading violations.
    for ln, h in iter_h2(lines):
        if INSTALL_HEADINGS.match(h):
            report.add("readme-install", "FAIL", "README.md", ln,
                       f"'## {h}' in README — move to HUMANS.md")
        if USAGE_HEADINGS.match(h):
            # Walkthrough threshold: does the section body contain a numbered list (3+ items)?
            if _has_procedural_steps_under(lines, ln):
                report.add("readme-usage", "WARN", "README.md", ln,
                           f"'## {h}' in README has procedural steps — move to HUMANS.md")
        if UNINSTALL_HEADINGS.match(h):
            report.add("readme-uninstall", "FAIL", "README.md", ln,
                       f"'## {h}' in README — move to HUMANS.md")
        if ENV_HEADINGS.match(h):
            report.add("readme-env", "FAIL", "README.md", ln,
                       f"'## {h}' in README — move to HUMANS.md")

    # Env-var table (column like CLAUDE_*, _MAX_, _PATH, or "Default" header).
    env_table = re.compile(r"^\|\s*(`?[A-Z][A-Z0-9_]{3,}`?)\s*\|")
    for i, line in enumerate(lines, 1):
        if env_table.match(line):
            report.add("readme-env", "WARN", "README.md", i,
                       "env-var-style table row in README — move to HUMANS.md")
            break

    # Tier links.
    body = "\n".join(lines)
    needed = {"HUMANS": "HUMANS.md", "AGENTS": "AGENTS.md"}
    missing = [label for label, target in needed.items() if target not in body]
    if missing:
        report.add("readme-no-tier-links", "WARN", "README.md", 0,
                   f"README does not link to: {', '.join(missing)}")


# --- README 7-part structure rules ------------------------------------------

_README_CENTERED_TITLE_RE = re.compile(r'<h1\s[^>]*align\s*=\s*["\']center', re.I)
_README_HIGHLIGHTS_RE = re.compile(
    r'^(highlights?|features?|highlighted features?|key features?|what.s included|what you get)$',
    re.I,
)
_README_DOCS_HEADING_RE = re.compile(r'^documentation$', re.I)
_README_LICENSE_RE = re.compile(r'(^##\s+licen[cs]e\b|©|\bcopyright\b)', re.I | re.M)
_README_BADGE_DIV_RE = re.compile(r'<div\s+align\s*=\s*["\']center["\']', re.I)
_README_SHIELD_RE = re.compile(r"shields\.io|img\.shields\.io", re.I)


def audit_readme_structure(root: Path, report: Report) -> None:
    """Enforce the 7-part README structure:
    1. Centered <h1> title
    2. Badges in <div align="center">
    3. '---' separator after badge block (within first 30 lines)
    4. Summary (1-3 paragraphs — not mechanically checkable)
    5. ## Highlights / ## Features section
    6. ## Documentation pipe-table
    7. License / copyright footer
    """
    path = root / "README.md"
    if not path.exists():
        return  # already caught by audit_readme
    lines = read(path)
    if not lines:
        return

    # 1. Centered title: first Markdown H1 must be replaced by <h1 align="center">.
    first_plain_h1 = next(
        (i for i, l in enumerate(lines, 1) if re.match(r'^#\s+\S', l)), None
    )
    has_html_title = any(_README_CENTERED_TITLE_RE.search(l) for l in lines)
    if first_plain_h1 and not has_html_title:
        report.add(
            "readme-title-not-centered", "WARN", "README.md", first_plain_h1,
            'H1 is plain Markdown — use <h1 align="center">Title</h1> (7-part spec)',
        )

    # 2. Badges in <div align="center"> with shields.io (first 35 lines).
    head = "\n".join(lines[:35])
    if not _README_BADGE_DIV_RE.search(head):
        report.add(
            "readme-no-badges", "WARN", "README.md", 0,
            'Missing <div align="center"> badge block (7-part spec part 2)',
        )
    elif not _README_SHIELD_RE.search(head):
        report.add(
            "readme-no-badges", "WARN", "README.md", 0,
            "Badge block should include shields.io badges (Python, license, CI, etc.)",
        )

    # 3. Separator: a bare '---' line must appear within the first 30 lines.
    separator_line = next(
        (i for i, l in enumerate(lines, 1) if l.strip() == "---"), None
    )
    if separator_line is None or separator_line > 30:
        report.add(
            "readme-no-separator", "WARN", "README.md", 0,
            "No '---' separator in first 30 lines (7-part spec: separator after badge block)",
        )

    # 4. Highlights / Features section.
    has_highlights = any(_README_HIGHLIGHTS_RE.match(h) for _, h in iter_h2(lines))
    if not has_highlights:
        report.add(
            "readme-no-highlights", "WARN", "README.md", 0,
            "Missing ## Highlights / ## Features section (7-part spec)",
        )

    # 5. Documentation pipe-table: ## Documentation heading followed by at least one | row.
    in_docs = False
    has_docs_table = False
    for line in lines:
        m = re.match(r'^##\s+(.+?)\s*$', line)
        if m:
            in_docs = bool(_README_DOCS_HEADING_RE.match(m.group(1).strip()))
        if in_docs and re.match(r'^\|', line):
            has_docs_table = True
            break
    if not has_docs_table:
        report.add(
            "readme-no-docs-table", "WARN", "README.md", 0,
            "Missing ## Documentation pipe-table (7-part spec)",
        )

    # 6. License / copyright footer: must appear in the last 30 lines.
    tail = "\n".join(lines[-30:] if len(lines) >= 30 else lines)
    if not _README_LICENSE_RE.search(tail):
        report.add(
            "readme-no-license-footer", "INFO", "README.md", 0,
            "No license or copyright notice in last 30 lines (7-part spec)",
        )


def _has_procedural_steps_under(lines: list[str], section_line: int) -> bool:
    # Count numbered list items (`1.`, `2.`, ...) in the section until next H2.
    count = 0
    for i in range(section_line, len(lines)):
        line = lines[i]
        if i > section_line - 1 and line.startswith("## "):
            break
        if re.match(r"^\d+\.\s+", line):
            count += 1
            if count >= 3:
                return True
    return False


# --- AGENTS rules -----------------------------------------------------------


def audit_agents(root: Path, report: Report) -> None:
    path = root / "AGENTS.md"
    if not path.exists():
        report.add("agents-missing", "INFO", "AGENTS.md", 0, "AGENTS.md not found (optional for non-LLM-onboarded repos)")
        return
    lines = read(path)

    for ln, h in iter_h2(lines):
        if re.match(r"^commit( convention|\b)", h, re.I):
            report.add("agents-commit", "WARN", "AGENTS.md", ln,
                       f"'## {h}' in AGENTS — commit conventions belong in CONTRIBUTING.md")

    # Stale "no automated test suite" claim.
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        for i, line in enumerate(lines, 1):
            if re.search(r"no automated test(s| suite)?", line, re.I):
                report.add("agents-stale-tests", "WARN", "AGENTS.md", i,
                           "AGENTS says 'no automated tests' but tests/ exists")
                break


# --- HUMANS presence --------------------------------------------------------


def audit_humans(root: Path, report: Report) -> None:
    if not (root / "HUMANS.md").exists():
        report.add("humans-missing", "WARN", "HUMANS.md", 0,
                   "HUMANS.md not found — authoritative run/use docs missing")


# --- Cross-file rules -------------------------------------------------------


def audit_duplicated_h2(root: Path, report: Report) -> None:
    readme = root / "README.md"
    humans = root / "HUMANS.md"
    if not (readme.exists() and humans.exists()):
        return
    readme_heads = {h for _, h in iter_h2(read(readme))}
    for ln, h in iter_h2(read(humans)):
        if h in readme_heads and h not in {"Documentation", "Further reading", "Requirements"}:
            report.add("dup-heading", "INFO", "HUMANS.md", ln,
                       f"H2 '{h}' also present in README (possible duplication)")


# --- Stale-ref detection ----------------------------------------------------

# Files whose role is to record history — skip stale-ref checks in these.
_STALE_REF_SKIP_FILES = {"CHANGELOG.md", "HISTORY.md", "RELEASES.md", "NEWS.md"}

# Extensions scanned when auto-discovering filename tokens in Markdown docs.
SCANNABLE_EXTENSIONS = {".sh", ".py", ".ts", ".tsx", ".go", ".rs"}

# Filenames that ALWAYS flag as stale, regardless of whether they exist on disk.
# Preserves legacy behavior for known-removed scripts.
KNOWN_STALE: set[str] = {"notify-send"}

# Filenames too generic to flag (short, or common entry-point conventions).
# Bias toward flagging — keep this list small.
_STALE_GENERIC_DENYLIST = {
    "run.sh", "test.sh", "main.go", "mod.rs", "lib.rs", "main.py",
    "main.ts", "setup.sh",
    # Illustrative placeholder filenames commonly used in guide/doc prose:
    "test.ts", "spec.ts", "file.ts", "test.tsx",
}


def _build_existing_filenames(root: Path) -> set[str]:
    """Build a set of all filenames present anywhere in the tree (fast membership check)."""
    skip = {"node_modules", ".git", "build", "dist", ".venv", "venv",
            "__pycache__", "target", ".yarn", ".cache", ".next", ".nuxt",
            "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
    names: set[str] = set()
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root)
            if not any(part in skip for part in rel.parts):
                names.add(p.name)
    return names


# Token pattern: word-boundary filename with a scannable extension.
# tsx before ts so the alternation matches the longer form first.
_FILENAME_TOKEN = re.compile(
    r"\b([\w][\w.-]*\.(?:tsx|sh|py|ts|go|rs))\b"
)

# Guard 1: backtick-quoted Owner/Repo citation on the same line.
# When present, all filename tokens on that line are cross-repo references.
_REPO_CITATION = re.compile(r"`[A-Za-z0-9_-]+/[A-Za-z0-9_-]+`")

# Guard 3: path-prefix patterns that indicate cross-repo file citations.
# Matches path-prefixed tokens like `deploy/script.sh`, `./deploy/script.sh`,
# or `internal/mcp/tools.go` inside backticks.
_CROSS_REPO_PATH_PREFIX = re.compile(
    r"`(?:\.?/)?(?:internal/[^/`]+/|deploy/|scripts/)[\w][\w.-]*\.(?:tsx|sh|py|ts|go|rs)`"
)

# Guard 3 lead-in: prose markers that introduce cross-repo citations.
_CITATION_LEAD_IN = re.compile(r"\b(?:Source|See|Reference):", re.I)

# Guard 4: filename token appears inside a backtick-quoted shell invocation.
# e.g. `node --loader ts-node/esm server.ts` — server.ts is an arg, not a repo file.
_SHELL_CMD_BACKTICK = re.compile(
    r"`(?:node|python3?|bun|deno|ts-node|tsx|go run|cargo run|bash|sh|zsh)\b[^`]*`",
    re.I,
)


def _in_shell_cmd_backtick(line: str, token_start: int) -> bool:
    """Return True if token_start falls inside a backtick-quoted shell invocation."""
    for m in _SHELL_CMD_BACKTICK.finditer(line):
        if m.start() <= token_start < m.end():
            return True
    return False


def _lines_in_code_blocks(lines: list[str]) -> set[int]:
    """Return the set of 1-based line numbers that fall inside fenced code blocks.

    Guard 2: fenced blocks whose first non-empty content line starts with ``{`` or
    ``[`` are JSON/data payloads and are included in the suppressed set regardless
    of the normal code-block rule (they are suppressed either way, but the flag is
    explicit for clarity).
    """
    inside: set[int] = set()
    in_fence = False
    block_lines: list[int] = []
    first_content: str = ""
    for i, line in enumerate(lines, 1):
        if line.startswith("```"):
            if not in_fence:
                # Entering a fence.
                in_fence = True
                block_lines = []
                first_content = ""
            else:
                # Exiting a fence — commit block_lines to inside.
                in_fence = False
                inside.update(block_lines)
            continue
        if in_fence:
            block_lines.append(i)
            if not first_content and line.strip():
                first_content = line.strip()
    return inside


def _is_generic(name: str) -> bool:
    if name in _STALE_GENERIC_DENYLIST:
        return True
    bare = name.rsplit(".", 1)[0]
    # Short bare stems are too ambiguous.
    if len(bare) <= 3:
        return True
    # Names starting with uppercase are likely proper nouns / service brands (e.g. Polar.sh, Docker.sh).
    if bare[0].isupper():
        return True
    # Names starting with underscore are suffix patterns, not real filenames (e.g. `_test.go`).
    if bare.startswith("_"):
        return True
    return False


def _build_cross_repo_basenames(docs: list[Path]) -> set[str]:
    """Pre-pass: collect basenames of files that appear with a cross-repo path prefix
    (e.g. ``deploy/``, ``scripts/``, ``internal/<pkg>/``) anywhere in the tracked docs.

    These basenames are known to belong to OTHER repos and should not flag as stale
    refs even when cited without a path prefix on a different line.  Respects
    code-block exclusion so that stale-changelog references don't poison the set.
    """
    basenames: set[str] = set()
    for md in docs:
        lines = read(md)
        code_block_lines = _lines_in_code_blocks(lines)
        for i, line in enumerate(lines, 1):
            if i in code_block_lines:
                continue
            for m in _CROSS_REPO_PATH_PREFIX.finditer(line):
                # Strip backticks and optional leading ./ then take basename.
                token = m.group(0).strip("`").lstrip("./")
                basename = token.split("/")[-1]
                if basename:
                    basenames.add(basename)
    return basenames


def audit_stale_refs(root: Path, report: Report) -> None:
    existing = _build_existing_filenames(root)
    tracked = _tracked_docs(root)

    # Cross-repo basename pre-pass (structural guard 5):
    # Basenames that appear elsewhere in this dossier with a path prefix like
    # `deploy/`, `scripts/`, or `internal/<pkg>/` belong to another repo.
    cross_repo_basenames = _build_cross_repo_basenames(
        [md for md in tracked if md.name not in _STALE_REF_SKIP_FILES]
    )

    # Track (doc_path, line, name) to avoid duplicate findings from overlapping rules.
    seen: set[tuple[str, int, str]] = set()

    for md in tracked:
        if md.name in _STALE_REF_SKIP_FILES:
            continue
        lines = read(md)
        code_block_lines = _lines_in_code_blocks(lines)
        rel = str(md.relative_to(root))

        for i, line in enumerate(lines, 1):
            # Guard 2 / existing: skip fenced code-block content.
            # Blocks whose first non-empty line starts with { or [ are JSON payloads
            # and are already included in code_block_lines by _lines_in_code_blocks.
            if i in code_block_lines:
                continue
            # Skip lines that are just URLs.
            if "http://" in line or "https://" in line:
                continue

            # Guard 1: line contains a backtick-quoted Owner/Repo citation.
            # All filename tokens on this line are cross-repo references.
            if _REPO_CITATION.search(line):
                continue

            # Guard 3: line contains a path-prefixed backtick token (deploy/,
            # scripts/, internal/<pkg>/) AND a citation lead-in (Source:/See:/Reference:)
            # or a Guard-1-style repo citation already handled above.
            if _CROSS_REPO_PATH_PREFIX.search(line) and (
                _CITATION_LEAD_IN.search(line) or _REPO_CITATION.search(line)
            ):
                continue

            # --- KNOWN_STALE: hard-coded names that always flag ---
            for name in KNOWN_STALE:
                pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")
                if pattern.search(line):
                    key = (rel, i, name)
                    if key not in seen:
                        seen.add(key)
                        report.add("stale-ref", "WARN", rel, i,
                                   f"references '{name}' (known-removed)")

            # --- Auto-scanner: filename tokens with scannable extensions ---
            for m in _FILENAME_TOKEN.finditer(line):
                name = m.group(1)
                if name in KNOWN_STALE:
                    continue  # already handled above
                if _is_generic(name):
                    continue
                if name in existing:
                    continue  # file exists in the tree — not stale

                # Slash-prefix guard: token immediately preceded by '/' means it
                # appears as a path component (e.g. internal/ops/server.go).
                start = m.start()
                if start > 0 and line[start - 1] == "/":
                    continue

                # Guard 4: token is inside a backtick-quoted shell invocation.
                if _in_shell_cmd_backtick(line, start):
                    continue

                # Cross-repo basename guard (pre-pass result):
                # basename seen with path prefix elsewhere in this dossier.
                if name in cross_repo_basenames:
                    continue

                key = (rel, i, name)
                if key not in seen:
                    seen.add(key)
                    report.add("stale-ref", "WARN", rel, i,
                               f"references '{name}' (not found anywhere in repo)")


def _tracked_docs(root: Path) -> list[Path]:
    out: list[Path] = []
    for md in root.rglob("*.md"):
        rel = md.relative_to(root)
        parts = rel.parts
        if any(p in {"node_modules", ".git", "build", "dist", ".venv", "target"} for p in parts):
            continue
        out.append(md)
    return out


# --- Spec drift framing -----------------------------------------------------


SPEC_DRIFT_PATTERNS = [
    re.compile(r"\bhistorical note\b", re.I),
    re.compile(r"\bwas removed\b", re.I),
    re.compile(r"\bpreviously\b", re.I),
    re.compile(r"\bdefensive\b(?! guard against impossible)", re.I),
    re.compile(r"\bdowngrade\b", re.I),
    re.compile(r"Resolved \(v\d+\.\d+\.\d+\):", re.I),
]


def audit_specs(root: Path, report: Report) -> None:
    specs_dir = root / "specs"
    if not specs_dir.is_dir():
        return
    for spec in specs_dir.rglob("spec.md"):
        if "active" in spec.parts:
            continue  # only apply to 'done' (implemented) specs
        lines = read(spec)
        rel = str(spec.relative_to(root))
        for i, line in enumerate(lines, 1):
            for pat in SPEC_DRIFT_PATTERNS:
                if pat.search(line):
                    report.add("spec-drift-framing", "INFO", rel, i,
                               f"drift-describing phrase: {line.strip()[:80]}")
                    break


# --- TODO.md current-state --------------------------------------------------


def audit_todo(root: Path, report: Report) -> None:
    todo = root / "TODO.md"
    if not todo.exists():
        return
    lines = read(todo)
    for ln, h in iter_h2(lines):
        if re.match(r"^(shipped|completed|done|implemented|current state|history)$", h, re.I):
            report.add("todo-current-state", "WARN", "TODO.md", ln,
                       f"'## {h}' — TODO.md is future-only; move shipped work out")


# --- Report emitters --------------------------------------------------------


def emit_text(report: Report) -> str:
    if not report.findings:
        return f"doc-audit: clean ({report.root})\n"
    buckets: dict[str, list[Finding]] = {}
    for f in report.findings:
        buckets.setdefault(f.severity, []).append(f)
    out = [f"doc-audit: {len(report.findings)} finding(s) at {report.root}", ""]
    for sev in ("FAIL", "WARN", "INFO"):
        rows = buckets.get(sev, [])
        if not rows:
            continue
        out.append(f"== {sev} ({len(rows)}) ==")
        for f in sorted(rows, key=lambda x: (x.path, x.line)):
            loc = f"{f.path}:{f.line}" if f.line else f.path
            out.append(f"  [{f.category}] {loc}  {f.message}")
        out.append("")
    return "\n".join(out)


def emit_md(report: Report) -> str:
    lines = [f"# doc-audit — {report.root}", ""]
    if not report.findings:
        lines.append("Clean. No tier violations detected.")
        return "\n".join(lines) + "\n"
    lines.append(f"**{len(report.findings)} finding(s).**")
    lines.append("")
    lines.append("| Severity | Category | File:Line | Message |")
    lines.append("|----------|----------|-----------|---------|")
    for f in sorted(report.findings, key=lambda x: (x.severity, x.path, x.line)):
        loc = f"`{f.path}:{f.line}`" if f.line else f"`{f.path}`"
        lines.append(f"| {f.severity} | `{f.category}` | {loc} | {f.message} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def emit_json(report: Report) -> str:
    return json.dumps(
        {
            "root": report.root,
            "findings": [
                {"category": f.category, "severity": f.severity, "path": f.path, "line": f.line, "message": f.message}
                for f in report.findings
            ],
        },
        indent=2,
    ) + "\n"


# --- CLI --------------------------------------------------------------------


def _load_skip_categories(root: Path) -> set[str]:
    """Load skip-category overrides from .doc-audit.json if present.

    Format::

        {"skip": ["humans-missing", "agents-missing", "readme-no-tier-links"]}

    Useful for non-software repos (fundraise dossiers, pure-docs repos) where
    tier checks for HUMANS.md / AGENTS.md don't apply.
    """
    cfg = root / ".doc-audit.json"
    if not cfg.exists():
        return set()
    try:
        import json as _json
        data = _json.loads(cfg.read_text(encoding="utf-8"))
        return set(data.get("skip", []))
    except Exception:
        return set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doc-audit", description=__doc__)
    ap.add_argument("--root", type=Path, help="Repo root (default: walk up from $PWD to .git)")
    ap.add_argument("--format", choices=("text", "md", "json"), default="text")
    ap.add_argument("--fail-on", default="",
                    help="Comma-separated category list; exit non-zero on any match.")
    ap.add_argument("--strict", action="store_true",
                    help="Alias for --fail-on=<all categories>.")
    ap.add_argument("--skip-categories", default="",
                    help="Comma-separated categories to suppress (also via .doc-audit.json).")
    ap.add_argument("--list-categories", action="store_true")
    args = ap.parse_args(argv)

    if args.list_categories:
        for cat, desc in sorted(CATEGORIES.items()):
            print(f"{cat:24s}  {desc}")
        return 0

    root = args.root.resolve() if args.root else find_repo_root(Path.cwd())
    report = Report(root=str(root))

    audit_readme(root, report)
    audit_readme_structure(root, report)
    audit_humans(root, report)
    audit_agents(root, report)
    audit_duplicated_h2(root, report)
    audit_stale_refs(root, report)
    audit_specs(root, report)
    audit_todo(root, report)

    # Build skip set from config file + CLI flag.
    skip_cats = _load_skip_categories(root)
    skip_cats |= {c.strip() for c in args.skip_categories.split(",") if c.strip()}
    if skip_cats:
        report.findings = [f for f in report.findings if f.category not in skip_cats]

    if args.format == "md":
        sys.stdout.write(emit_md(report))
    elif args.format == "json":
        sys.stdout.write(emit_json(report))
    else:
        sys.stdout.write(emit_text(report))

    fail_cats = set(CATEGORIES) if args.strict else {c.strip() for c in args.fail_on.split(",") if c.strip()}
    if fail_cats:
        if any(f.category in fail_cats for f in report.findings):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
