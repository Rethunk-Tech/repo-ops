"""Tests for TASK A (ECO_FILTER_CHOICES + inventory new ecosystems) and
TASK B (--log-level / -v) from issues #16 and #22."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from repo_ops_core.cli import (
    ECO_FILTER_CHOICES,
    build_upgrade_parser,
)
from repo_ops_core.process import configure_logging
from repo_ops_inventory.counting import (
    _kind_matches,
    count_actions,
    count_container,
    count_precommit,
    scan,
)
from repo_ops_inventory.scan import build_inventory_parser
from repo_ops_inventory.scan import main as inventory_main

# ---------------------------------------------------------------------------
# TASK A: ECO_FILTER_CHOICES coverage
# ---------------------------------------------------------------------------


def test_eco_filter_choices_includes_precommit_and_actions():
    """precommit and actions must appear in ECO_FILTER_CHOICES (#16)."""
    assert "precommit" in ECO_FILTER_CHOICES
    assert "actions" in ECO_FILTER_CHOICES


def test_inventory_parser_accepts_precommit_filter():
    """inventory --filter precommit must parse without error."""
    parser = build_inventory_parser()
    args = parser.parse_args(["--filter", "precommit"])
    assert args.eco_filter == "precommit"


def test_inventory_parser_accepts_actions_filter():
    """inventory --filter actions must parse without error."""
    parser = build_inventory_parser()
    args = parser.parse_args(["--filter", "actions"])
    assert args.eco_filter == "actions"


def test_upgrade_parser_accepts_precommit_filter():
    """plan/apply --filter precommit must parse without error."""
    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--filter", "precommit"])
    assert args.eco_filter == "precommit"


def test_upgrade_parser_accepts_actions_filter():
    """plan/apply --filter actions must parse without error."""
    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--filter", "actions"])
    assert args.eco_filter == "actions"


# ---------------------------------------------------------------------------
# TASK A: inventory counting for new ecosystems
# ---------------------------------------------------------------------------


def test_count_precommit_counts_rev_pins(tmp_path: Path):
    """count_precommit returns the correct pin count from .pre-commit-config.yaml."""
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(
        "repos:\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v4.5.0\n"
        "    hooks:\n"
        "      - id: trailing-whitespace\n"
        "  - repo: https://github.com/psf/black\n"
        "    rev: 24.1.0\n"
        "    hooks:\n"
        "      - id: black\n",
        encoding="utf-8",
    )
    direct, versions = count_precommit(tmp_path)
    assert direct == 2
    assert any("pre-commit-hooks" in v for v in versions)
    assert any("black" in v for v in versions)


def test_count_actions_counts_uses_pins(tmp_path: Path):
    """count_actions returns the correct pin count from .github/workflows/."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n",
        encoding="utf-8",
    )
    direct, versions = count_actions(tmp_path)
    assert direct == 2
    assert "actions/checkout@v4" in versions
    assert "actions/setup-python@v5" in versions


def test_count_container_counts_image_pins(tmp_path: Path):
    """count_container returns the correct pin count from Dockerfile."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.12-slim\nFROM node:20-alpine AS builder\n",
        encoding="utf-8",
    )
    direct, versions = count_container(tmp_path)
    assert direct == 2
    assert any("python:3.12-slim" in v for v in versions)
    assert any("node:20-alpine" in v for v in versions)


def test_inventory_scan_includes_precommit_kind(tmp_path: Path):
    """scan() populates 'precommit' kind when .pre-commit-config.yaml is present."""
    cfg = tmp_path / ".pre-commit-config.yaml"
    cfg.write_text(
        "repos:\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v4.5.0\n"
        "    hooks: [{id: trailing-whitespace}]\n",
        encoding="utf-8",
    )
    rows = scan(tmp_path, flat=True)
    assert len(rows) == 1
    assert "precommit" in rows[0]["kinds"]
    assert rows[0]["direct"] >= 1


def test_inventory_scan_includes_actions_kind(tmp_path: Path):
    """scan() populates 'actions' kind when .github/workflows/ has a workflow."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    rows = scan(tmp_path, flat=True)
    assert len(rows) == 1
    assert "actions" in rows[0]["kinds"]
    assert rows[0]["direct"] >= 1


def test_kind_matches_new_ecosystems():
    """_kind_matches handles precommit and actions kinds correctly."""
    assert _kind_matches({"precommit"}, "precommit")
    assert _kind_matches({"actions"}, "actions")
    assert not _kind_matches({"actions"}, "precommit")
    assert not _kind_matches({"-"}, "actions")


def test_inventory_main_filter_precommit_excludes_node(tmp_path: Path, capsys):
    """inventory --filter precommit suppresses rows that have only node kind."""
    org = tmp_path / "myorg"
    proj_node = org / "proj-node"
    proj_node.mkdir(parents=True)
    (proj_node / "package.json").write_text(json.dumps({"dependencies": {"a": "^1"}}))

    proj_pre = org / "proj-pre"
    proj_pre.mkdir(parents=True)
    (proj_pre / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v4.5.0\n"
        "    hooks: [{id: trailing-whitespace}]\n",
        encoding="utf-8",
    )

    inventory_main([str(tmp_path), "--json", "--filter", "precommit"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    names = [r["name"] for r in payload["rows"]]
    # Only the precommit project should appear after filtering
    assert not any("proj-node" in n for n in names), (
        "node-only project must be filtered out"
    )
    assert any("proj-pre" in n for n in names), "precommit project must appear"


# ---------------------------------------------------------------------------
# TASK B: --log-level / -v
# ---------------------------------------------------------------------------


def test_log_level_argument_registered_on_inventory_parser():
    """inventory parser must accept --log-level."""
    parser = build_inventory_parser()
    args = parser.parse_args(["--log-level", "info"])
    assert args.log_level == "info"


def test_log_level_argument_registered_on_upgrade_parser():
    """plan/apply parser must accept --log-level."""
    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--log-level", "warning"])
    assert args.log_level == "warning"


def test_log_level_choices():
    """--log-level accepts debug/info/warning/error only."""
    parser = build_upgrade_parser(apply=False)
    for level in ("debug", "info", "warning", "error"):
        args = parser.parse_args(["--log-level", level])
        assert args.log_level == level
    with pytest.raises(SystemExit):
        parser.parse_args(["--log-level", "trace"])


def test_configure_logging_sets_root_level():
    """configure_logging sets the stdlib root logger to the given level."""
    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
    configure_logging("info")
    assert logging.getLogger().level == logging.INFO
    # Restore
    configure_logging("warning")


def test_configure_logging_debug_implies_verbose(monkeypatch):
    """configure_logging('debug') sets VERBOSE flag for subprocess echo."""
    import repo_ops_core.process as proc_mod

    monkeypatch.setattr(proc_mod, "VERBOSE", False)
    configure_logging("debug")
    assert proc_mod.VERBOSE is True
    # Restore
    monkeypatch.setattr(proc_mod, "VERBOSE", False)


def test_configure_logging_noop_on_none():
    """configure_logging(None) must not touch root logger level."""
    before = logging.getLogger().level
    configure_logging(None)
    assert logging.getLogger().level == before


def test_verbose_flag_implies_debug_when_no_log_level():
    """wire_verbose with --verbose and no --log-level must reach debug level."""
    import repo_ops_core.process as proc_mod
    from repo_ops_core.cli import wire_verbose

    parser = build_inventory_parser()
    args = parser.parse_args(["--verbose"])
    old_verbose = proc_mod.VERBOSE
    try:
        wire_verbose(args)
        assert proc_mod.VERBOSE is True
        assert logging.getLogger().level == logging.DEBUG
    finally:
        proc_mod.VERBOSE = old_verbose


def test_verbose_and_log_level_combined():
    """--verbose --log-level warning: subprocess echo on, stdlib at WARNING."""
    import repo_ops_core.process as proc_mod
    from repo_ops_core.cli import wire_verbose

    parser = build_upgrade_parser(apply=False)
    args = parser.parse_args(["--verbose", "--log-level", "warning"])
    old_verbose = proc_mod.VERBOSE
    try:
        wire_verbose(args)
        assert proc_mod.VERBOSE is True
        assert logging.getLogger().level == logging.WARNING
    finally:
        proc_mod.VERBOSE = old_verbose
