"""Regression tests for bun outdated table parsing.

Covers:
  * _changes_from_bun_text: ANSI escape codes stripped, (dev)/(peer)/(optional)
    suffixes removed, banner line and box borders skipped, literal package named
    "bun" accepted (i.e. NOT filtered by name).

No subprocess or network calls are made.
"""

from __future__ import annotations

from repo_ops_js_bun.bun import (
    _changes_from_bun_text,
    _valid_bun_package_name,
)

# ---------------------------------------------------------------------------
# Captured fixture: realistic bun outdated ANSI output
# ---------------------------------------------------------------------------

# │ is U+2502 (box drawings light vertical), used as the column separator.
_BOX_V = "│"

# Fixture string with ANSI colour codes, (dev) group suffixes, a literal
# package named "bun", and a banner that MUST be ignored.
_BUN_OUTDATED_ANSI = (
    "\x1b[1mbun outdated\x1b[0m v1.3.0 (abc12345)\n"
    "Resolving dependencies...\n"
    "\n"
    f"┌──────────"
    f"┬─────────"
    f"┬─────────"
    f"┬────────┐\n"
    f"{_BOX_V} Package        {_BOX_V} Current {_BOX_V} Update  {_BOX_V} Latest  {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # @types/bun (dev) — must strip (dev) suffix
    f"{_BOX_V} \x1b[32m@types/bun\x1b[0m \x1b[90m(dev)\x1b[0m {_BOX_V} 1.1.6   {_BOX_V} 1.1.10  {_BOX_V} 1.1.10  {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # react (no suffix)
    f"{_BOX_V} \x1b[32mreact\x1b[0m         {_BOX_V} 18.2.0  {_BOX_V} 18.3.0  {_BOX_V} 18.3.0  {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # typescript (dev) — must strip (dev) suffix
    f"{_BOX_V} \x1b[32mtypescript\x1b[0m \x1b[90m(dev)\x1b[0m {_BOX_V} 5.3.3   {_BOX_V} 5.6.2   {_BOX_V} 5.6.2   {_BOX_V}\n"
    f"├──────────"
    f"┼─────────"
    f"┼─────────"
    f"┼────────┤\n"
    # literal package named "bun" — must NOT be filtered by name
    f"{_BOX_V} \x1b[32mbun\x1b[0m           {_BOX_V} 1.1.0   {_BOX_V} 1.2.0   {_BOX_V} 1.3.0   {_BOX_V}\n"
    f"└──────────"
    f"┴─────────"
    f"┴─────────"
    f"┴────────┘\n"
)


# ---------------------------------------------------------------------------
# _changes_from_bun_text — ANSI parser regression
# ---------------------------------------------------------------------------


def test_bun_text_parser_strips_ansi_and_parses_packages() -> None:
    changes, _behind = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    names = {c.name for c in changes}
    # All four packages should be detected
    assert "@types/bun" in names
    assert "react" in names
    assert "typescript" in names
    assert "bun" in names  # literal package named "bun" is valid


def test_bun_text_parser_strips_dev_suffix() -> None:
    changes, _ = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    names = {c.name for c in changes}
    # Must strip group suffix — no "(dev)" in any name
    assert not any("(dev)" in n for n in names)


def test_bun_text_parser_skips_banner_and_borders() -> None:
    """Banner line and box-border rows must not produce Change entries."""
    changes, _ = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    # "Package" header and box lines produce no Change
    assert all(c.name != "Package" for c in changes)
    # No entry whose from_ver is empty
    assert all(c.from_ver for c in changes)


def test_bun_text_parser_correct_versions() -> None:
    changes, _ = _changes_from_bun_text(_BUN_OUTDATED_ANSI)
    by_name = {c.name: c for c in changes}
    assert by_name["@types/bun"].from_ver == "1.1.6"
    assert by_name["@types/bun"].to == "1.1.10"
    assert by_name["react"].from_ver == "18.2.0"
    assert by_name["react"].to == "18.3.0"
    # the Latest column (1.3.0) is the target, not the in-range Update (1.2.0)
    assert by_name["bun"].from_ver == "1.1.0"
    assert by_name["bun"].to == "1.3.0"


def test_bun_text_parser_peer_optional_suffix_stripped() -> None:
    """(peer) and (optional) group suffixes are stripped like (dev)."""
    fixture = (
        f"{_BOX_V} foo \x1b[90m(peer)\x1b[0m      {_BOX_V} 1.0.0  {_BOX_V} 2.0.0  {_BOX_V} 2.0.0  {_BOX_V}\n"
        f"{_BOX_V} bar \x1b[90m(optional)\x1b[0m  {_BOX_V} 1.0.0  {_BOX_V} 1.1.0  {_BOX_V} 1.1.0  {_BOX_V}\n"
    )
    changes, _ = _changes_from_bun_text(fixture)
    names = {c.name for c in changes}
    assert "foo" in names
    assert "bar" in names
    assert not any("(" in n for n in names)


def test_bun_text_parser_empty_output_no_changes() -> None:
    changes, behind = _changes_from_bun_text("")
    assert changes == []
    assert behind == []


# ---------------------------------------------------------------------------
# _valid_bun_package_name — edge cases
# ---------------------------------------------------------------------------


def test_valid_bun_package_name_literal_bun_accepted() -> None:
    """A package literally named 'bun' must not be rejected."""
    assert _valid_bun_package_name("bun") is True


def test_valid_bun_package_name_header_rejected() -> None:
    assert _valid_bun_package_name("Package") is False


def test_valid_bun_package_name_dash_prefix_rejected() -> None:
    assert _valid_bun_package_name("-invalid") is False


def test_valid_bun_package_name_scoped_accepted() -> None:
    assert _valid_bun_package_name("@types/node") is True
