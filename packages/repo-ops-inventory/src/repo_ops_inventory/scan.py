#!/usr/bin/env python3
"""Count dependencies per project (direct + reachable versions + on-disk).

Usage: repo-ops inventory [root_dir ...]
Defaults: standard local roots from constants.DEFAULT_ROOTS

Columns:
  direct    Declared in manifest(s) (package.json / go.mod / Cargo.toml / pyproject)
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
from pathlib import Path

from repo_ops_core.cli import (
    add_eco_filter_argument,
    add_flat_argument,
    add_json_argument,
    add_roots_argument,
    add_verbose_argument,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.table import column_width, format_cell, join_row

from repo_ops_inventory.counting import (
    _go_cache_path,
    _kind_matches,
    load_go_cache,
    save_go_cache,
    scan,
)


def _print_inventory_table(all_rows: list[dict]) -> None:
    """Emit the human-readable inventory table."""
    names = [r["name"] for r in all_rows]
    kinds = [r["kinds_s"] for r in all_rows]
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

    td = tv = ti = te = tm = 0
    for r in all_rows:
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
        td += r["direct"]
        tv += r["versions_n"]
        ti += r["installed"]
        te += r["edges"]
        tm += r["manifests"]

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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="repo-ops inventory",
        description="Count dependencies per project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_roots_argument(parser)
    add_json_argument(parser, dest="json_out")
    parser.add_argument(
        "--sort",
        choices=["name", "direct", "versions", "on-disk", "edges"],
        default="name",
        help="Sort column: name (asc), others descending. Default: name.",
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
    args = parser.parse_args(argv)
    wire_verbose(args)

    cache_path = _go_cache_path()
    go_cache: dict | None = None
    if not args.no_cache:
        go_cache = load_go_cache(cache_path)

    roots = resolve_scan_roots(list(args.roots), parser)
    scanned_roots = list(roots)
    all_rows = []
    for r in roots:
        all_rows.extend(scan(Path(r), flat=args.flat, go_cache=go_cache))

    if not args.no_cache and go_cache is not None:
        save_go_cache(cache_path, go_cache)

    for r in all_rows:
        r["kinds_s"] = ",".join(sorted(r["kinds"]))
        r["versions_n"] = len(r["versions"])

    if args.eco_filter:
        all_rows = [r for r in all_rows if _kind_matches(r["kinds"], args.eco_filter)]

    sort_key = args.sort
    if sort_key == "name":
        all_rows.sort(key=lambda x: x["name"])
    elif sort_key == "direct":
        all_rows.sort(key=lambda x: x["direct"], reverse=True)
    elif sort_key == "versions":
        all_rows.sort(key=lambda x: x["versions_n"], reverse=True)
    elif sort_key == "on-disk":
        all_rows.sort(key=lambda x: x["installed"], reverse=True)
    elif sort_key == "edges":
        all_rows.sort(key=lambda x: x["edges"], reverse=True)

    if args.json_out:
        td = tv = ti = te = 0
        json_rows = []
        for r in all_rows:
            json_rows.append(
                {
                    "name": r["name"],
                    "kinds": sorted(r["kinds"]),
                    "direct": r["direct"],
                    "versions": r["versions_n"],
                    "on_disk": r["installed"],
                    "edges": r["edges"],
                }
            )
            td += r["direct"]
            tv += r["versions_n"]
            ti += r["installed"]
            te += r["edges"]
        print(
            json.dumps(
                {
                    "rows": json_rows,
                    "totals": {
                        "direct": td,
                        "versions": tv,
                        "on_disk": ti,
                        "edges": te,
                    },
                    "scanned_roots": scanned_roots,
                },
                indent=2,
            )
        )
        return

    _print_inventory_table(all_rows)


if __name__ == "__main__":
    main()
