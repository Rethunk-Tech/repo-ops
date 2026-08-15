#!/usr/bin/env python3
"""Count dependencies per project (direct + reachable versions + on-disk).

Usage: repo-ops inventory [root_dir ...]
Defaults: standard local roots from constants.DEFAULT_ROOTS

Columns:
  direct    Declared in manifest(s) (package.json / go.mod / Cargo.toml /
            pyproject / Pipfile / requirements.in)
  versions  Distinct name@version pairs reachable across lockfiles + build graph.
            For Go uses `go mod graph` (all reachable versions, NOT just MVS-selected).
            For Node parses yarn/npm/pnpm/bun lockfiles for every resolved version.
            For Rust parses Cargo.lock [[package]] blocks.
            Multiple major-version copies of same crate/lib counted separately.
  on-disk   Actual node_modules/**/package.json instance count (true sprawl,
            includes nested duplicates from version conflicts).
  manifests Number of manifest files found (depth ≥ 1).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from repo_ops_core.cli import (
    add_eco_filter_argument,
    add_flat_argument,
    add_json_argument,
    add_json_out_argument,
    add_roots_argument,
    add_verbose_argument,
    positive_int,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.filesystem import write_json_atomic
from repo_ops_core.table import column_width, format_cell, join_row

from repo_ops_inventory.counting import (
    _go_cache_path,
    _kind_matches,
    load_go_cache,
    restrict_row_to_eco,
    save_go_cache,
    scan,
)


def _totals(rows: list[dict]) -> dict[str, int]:
    """Sum manifests/direct/versions/on-disk/edges across rows."""
    return {
        "manifests": sum(r["manifests"] for r in rows),
        "direct": sum(r["direct"] for r in rows),
        "versions": sum(r["versions_n"] for r in rows),
        "on_disk": sum(r["installed"] for r in rows),
        "edges": sum(r["edges"] for r in rows),
    }


def _print_inventory_table(all_rows: list[dict], top: int | None = None) -> None:
    """Emit the human-readable inventory table.

    ``top`` caps the number of displayed rows; TOTAL is always computed over the
    full row set and a "… and N more" line is appended when rows are truncated.
    """
    # Totals over the FULL set first, so truncating the display never undercounts.
    totals = _totals(all_rows)
    tm, td, tv, ti, te = (
        totals["manifests"],
        totals["direct"],
        totals["versions"],
        totals["on_disk"],
        totals["edges"],
    )

    shown = all_rows[:top] if top is not None else all_rows
    names = [r["name"] for r in shown]
    kinds = [r["kinds_s"] for r in shown]
    name_w = column_width(names, "project")
    kind_w = column_width(kinds, "kinds", cap=40)

    header_cells = [
        format_cell("project", name_w),
        format_cell("kinds", kind_w),
        format_cell("mans", 4, align="right"),
        format_cell("direct", 6, align="right"),
        format_cell("versions", 8, align="right"),
        format_cell("on-disk", 7, align="right"),
        format_cell("edges", 7, align="right"),
    ]
    header = join_row(header_cells)
    print(header)
    print("-" * len(header))

    for r in shown:
        k = r["kinds_s"]
        print(
            join_row(
                [
                    format_cell(r["name"], name_w),
                    format_cell(k, kind_w),
                    format_cell(str(r["manifests"]), 4, align="right"),
                    format_cell(str(r["direct"]), 6, align="right"),
                    format_cell(str(r["versions_n"]), 8, align="right"),
                    format_cell(str(r["installed"]), 7, align="right"),
                    format_cell(str(r["edges"]), 7, align="right"),
                ]
            )
        )

    if top is not None and len(all_rows) > top:
        print(f"... and {len(all_rows) - top} more")

    print("-" * len(header))
    print(
        join_row(
            [
                format_cell("TOTAL", name_w),
                format_cell("", kind_w),
                format_cell(str(tm), 4, align="right"),
                format_cell(str(td), 6, align="right"),
                format_cell(str(tv), 8, align="right"),
                format_cell(str(ti), 7, align="right"),
                format_cell(str(te), 7, align="right"),
            ]
        )
    )


_SORT_KEYS: dict[str, tuple[Callable[[dict], str | int], bool]] = {
    "name": (lambda x: x["name"], False),
    "direct": (lambda x: x["direct"], True),
    "versions": (lambda x: x["versions_n"], True),
    "on-disk": (lambda x: x["installed"], True),
    "edges": (lambda x: x["edges"], True),
}


def build_inventory_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops inventory",
        description="Count dependencies per project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_inventory_arguments(parser)
    return parser


def _add_inventory_arguments(parser: argparse.ArgumentParser) -> None:
    add_roots_argument(parser)
    add_json_argument(parser, dest="json_out")
    add_json_out_argument(parser)
    parser.add_argument(
        "--sort",
        choices=["name", "direct", "versions", "on-disk", "edges"],
        default="name",
        help="Sort column: name (asc), others descending. Default: name.",
    )
    parser.add_argument(
        "--top",
        type=positive_int,
        default=None,
        help="Show only the top N rows in human output (TOTAL covers all rows).",
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=8,
        help="Max parallel workers for the Go module pool (default: 8).",
    )
    add_eco_filter_argument(parser)
    add_flat_argument(parser)
    parser.add_argument(
        "--no-cache",
        dest="no_cache",
        action="store_true",
        help="Bypass XDG Go module result cache.",
    )
    add_verbose_argument(parser)


def main(argv: list[str] | None = None) -> int:
    parser = build_inventory_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)
    if getattr(args, "json_report_path", None) and not args.json_out:
        parser.error("--json-out requires --json")

    cache_path = _go_cache_path()
    go_cache: dict | None = None
    if not args.no_cache:
        go_cache = load_go_cache(cache_path)

    roots = resolve_scan_roots(list(args.roots), parser)
    scanned_roots = list(roots)
    all_rows = []
    for root_path in roots:
        all_rows.extend(
            scan(Path(root_path), flat=args.flat, go_cache=go_cache, jobs=args.jobs)
        )

    if not args.no_cache and go_cache is not None:
        save_go_cache(cache_path, go_cache)

    if args.eco_filter:
        all_rows = [r for r in all_rows if _kind_matches(r["kinds"], args.eco_filter)]
        for r in all_rows:
            restrict_row_to_eco(r, args.eco_filter)

    for r in all_rows:
        r["kinds_s"] = ",".join(sorted(r["kinds"]))
        r["versions_n"] = len(r["versions"])

    sort_key_fn, sort_reverse = _SORT_KEYS[args.sort]
    all_rows.sort(key=sort_key_fn, reverse=sort_reverse)

    if args.json_out:
        # --json always emits ALL rows (--top only caps human output).
        json_rows = [
            {
                "name": r["name"],
                "kinds": sorted(r["kinds"]),
                "manifests": r["manifests"],
                "direct": r["direct"],
                "versions": r["versions_n"],
                "on_disk": r["installed"],
                "edges": r["edges"],
            }
            for r in all_rows
        ]
        totals = _totals(all_rows)
        # Build the payload once so the stdout and --json-out paths can never
        # diverge on schema_version/roots/totals (#101 + #104).
        # ``summary`` mirrors the human footer roll-up (docs/usage.md JSON contract).
        payload = {
            "schema_version": 1,
            "rows": json_rows,
            "totals": totals,
            "summary": {
                "projects": len(json_rows),
                **totals,
            },
            "roots": scanned_roots,
        }
        json_report_path = getattr(args, "json_report_path", None)
        if json_report_path:
            written = write_json_atomic(
                Path(json_report_path), payload, compact=args.compact
            )
            print(written)
        else:
            print(json.dumps(payload, indent=None if args.compact else 2))
        return 0

    _print_inventory_table(all_rows, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
