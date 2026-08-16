"""Coverage for Deno counting and the --flat scan path (counting.py).

Targets:
  - count_deno: direct deps from deno.json imports map, resolved specifiers
    from deno.lock (npm/jsr/specifiers sections).
  - scan(root, flat=True): single-project scan mixing a deno manifest with a
    Python manifest in the same directory.
  - _flat_project_name: the dotted-name fallback branch (Path.name starting
    with "." skips the fast-path early return).
"""

import json
from pathlib import Path

from repo_ops_inventory.counting import _flat_project_name, count_deno, scan


def test_count_deno_direct_and_versions(tmp_path: Path) -> None:
    (tmp_path / "deno.json").write_text(
        json.dumps(
            {
                "imports": {
                    "std/": "https://deno.land/std/",
                    "left-pad": "npm:left-pad@^1.3.0",
                }
            }
        )
    )
    (tmp_path / "deno.lock").write_text(
        json.dumps(
            {
                "version": "4",
                "specifiers": {"npm:left-pad@^1.3.0": "npm:left-pad@1.3.0"},
                "npm": {"left-pad@1.3.0": {"integrity": "sha512-x"}},
                "jsr": {"@std/assert@1.0.0": {"integrity": "sha512-y"}},
            }
        )
    )
    direct, versions = count_deno(tmp_path)
    assert direct == 2
    assert "npm:left-pad@1.3.0" in versions
    assert "jsr:@std/assert@1.0.0" in versions


def test_count_deno_no_manifests_returns_empty(tmp_path: Path) -> None:
    assert count_deno(tmp_path) == (0, set())


def test_scan_flat_mixed_deno_and_python(tmp_path: Path) -> None:
    """scan(root, flat=True) over a dir with deno.json + deno.lock + pyproject."""
    (tmp_path / "deno.json").write_text(
        json.dumps(
            {
                "imports": {
                    "std/": "https://deno.land/std/",
                    "left-pad": "npm:left-pad@^1.3.0",
                }
            }
        )
    )
    (tmp_path / "deno.lock").write_text(
        json.dumps(
            {
                "version": "4",
                "specifiers": {"npm:left-pad@^1.3.0": "npm:left-pad@1.3.0"},
                "npm": {"left-pad@1.3.0": {"integrity": "sha512-x"}},
                "jsr": {},
            }
        )
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["a"]\n')

    rows = scan(tmp_path, flat=True)
    assert len(rows) == 1
    row = rows[0]

    # 2 deno imports + 1 python dependency
    assert row["direct"] == 3
    assert "deno" in row["kinds"]
    assert any(k.startswith("py(") for k in row["kinds"])
    assert "npm:left-pad@1.3.0" in row["versions"]
    assert row["name"] == tmp_path.name


def test_scan_flat_non_directory_root_returns_empty(tmp_path: Path) -> None:
    """flat=True with a non-directory root yields no rows (defensive branch)."""
    missing = tmp_path / "no-such-file"
    assert not missing.exists()
    assert scan(missing, flat=True) == []


def test_flat_project_name_dotted_directory_fallback(tmp_path: Path) -> None:
    """Directory names starting with '.' skip the fast-path return (#101)."""
    hidden = tmp_path / ".hidden-project"
    hidden.mkdir()
    assert _flat_project_name(hidden) == ".hidden-project"
