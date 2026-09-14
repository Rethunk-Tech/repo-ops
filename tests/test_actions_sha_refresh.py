"""Tests for SHA-pin digest refresh within a major and cross-major
re-pin under --latest.

Keeps an action pinned by commit digest (supply-chain hardening) but advances
the SHA to the newest vN.M.P release within its current major — the same kind of
bump Dependabot makes when it security-pins by commit.  With upgrade_mode=latest,
also crosses major boundaries (Dependabot parity for major-bump PRs).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import scan_actions

# v6.0.2 commit (the current pin) and v6.0.3 commit (the within-major refresh target).
_OLD = "de0fac2e4500dabe0009e67214ff5f5447ce83dd"
_NEW = "11bd71901bbe5b1630ceea73d27597364c9af683"
_OTHER = "ffffffffffffffffffffffffffffffffffffffff"
# v7.0.1 commit — the cross-major target for cross-major tests.
_V7 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _make_tags_proc(*lines: str) -> MagicMock:
    return MagicMock(returncode=0, stdout="\n".join(lines) + "\n", stderr="")


def _wf(tmp_path: Path, content: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(content, encoding="utf-8")
    return wf


def test_refresh_advances_digest_within_major_and_keeps_comment(tmp_path: Path) -> None:
    """# v6 SHA pin at v6.0.2 → digest rewritten to v6.0.3's commit; # v6 preserved."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}  # v6\n",
    )
    tags_proc = _make_tags_proc(
        f"v6.0.3 {_NEW}",
        f"v6.0.2 {_OLD}",
        f"v6.0.1 {_OTHER}",
        f"v6 {_OTHER}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1, bumps
    b = checkout[0]
    assert b.pin == _OLD
    assert b.latest == _NEW
    assert "refresh SHA digest to v6.0.3" in (b.detail or "")
    assert "report only" not in (b.detail or "").lower()

    text = wf.read_text(encoding="utf-8")
    assert _NEW in text
    assert _OLD not in text
    assert "# v6" in text  # digest stays pinned; audit hint preserved


def test_refresh_rewrites_exact_comment_to_new_release(tmp_path: Path) -> None:
    """# v6.0.2 hint on a within-major refresh to v6.0.3 → # v6.0.3; trailing text kept."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD} #v6.0.2 audited\n",
    )
    tags_proc = _make_tags_proc(f"v6.0.3 {_NEW}", f"v6.0.2 {_OLD}", f"v6 {_NEW}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    assert wf.read_text(encoding="utf-8").endswith(f"@{_NEW} #v6.0.3 audited\n")


def test_refresh_resolves_major_via_tags_api_without_comment(tmp_path: Path) -> None:
    """No # vN comment: the major is resolved from the tags API, then refreshed."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}\n",
    )
    tags_proc = _make_tags_proc(
        f"v6.0.3 {_NEW}",
        f"v6.0.2 {_OLD}",  # current SHA → v6.0.2 → major 6
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    assert checkout[0].latest == _NEW
    assert _NEW in wf.read_text(encoding="utf-8")


def test_refresh_already_current_is_report_only(tmp_path: Path) -> None:
    """When the pin is already the newest in-major digest, nothing is rewritten."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_NEW}  # v6\n",
    )
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(
        f"v6.0.3 {_NEW}",  # newest in-major points at the current pin
        f"v6.0.2 {_OLD}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.latest == _NEW
    assert b.detail == "SHA-pinned; report only"
    assert wf.read_text(encoding="utf-8") == original


def test_refresh_off_by_default_keeps_report_only(tmp_path: Path) -> None:
    """Without refresh_sha, a SHA pin stays report-only even when a newer digest exists."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}  # v6\n",
    )
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(f"v6.0.3 {_NEW}", f"v6.0.2 {_OLD}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].detail == "SHA-pinned; report only"
    assert wf.read_text(encoding="utf-8") == original


def test_refresh_two_part_tags_orders_by_semver_and_rewrites_comment(tmp_path: Path) -> None:
    """gh-actions case: v1.10 > v1.9 despite two-part tags; floating v1 still at v1.9.

    The sub-path action's tags are fetched from its owner/repo.
    """
    action = "Rethunk-Tech/gh-actions/setup-go"
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: {action}@{_OLD} # v1.9\n",
    )
    tags_proc = _make_tags_proc(f"v1.10 {_NEW}", f"v1.9 {_OLD}", f"v1.8 {_OTHER}", f"v1 {_OLD}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc) as run_mock,
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    assert any("repos/Rethunk-Tech/gh-actions/tags" in c.args[0] for c in run_mock.call_args_list)
    checkout = [b for b in bumps if b.action == action]
    assert len(checkout) == 1, bumps
    assert checkout[0].latest == _NEW
    assert "refresh SHA digest to v1.10" in (checkout[0].detail or "")
    assert wf.read_text(encoding="utf-8").endswith(f"@{_NEW} # v1.10\n")


def test_refresh_mixed_two_and_three_part_tags_picks_highest(tmp_path: Path) -> None:
    """v1.10 outranks v1.9.3; a bare floating v1 on a newer commit is never a release."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD} # v1.9.3\n",
    )
    tags_proc = _make_tags_proc(
        f"v1 {_V7}", f"v1.9.3 {_OLD}", f"v1.10.0 {_NEW}", f"v1.10 {_NEW}", f"v1.9 {_OTHER}"
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].latest == _NEW
    assert _NEW in wf.read_text(encoding="utf-8")


def test_refresh_bare_major_only_is_report_only(tmp_path: Path) -> None:
    """A floating v1 on a newer commit with no newer vN.M release does not move the pin."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD} # v1.9\n",
    )
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(f"v1 {_NEW}", f"v1.9 {_OLD}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    assert [b.detail for b in bumps if b.action == "actions/checkout"] == [
        "SHA-pinned; report only"
    ]
    assert wf.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Cross-major SHA re-pin tests
# ---------------------------------------------------------------------------


def test_cross_major_refresh_latest_updates_sha_and_comment(tmp_path: Path) -> None:
    """upgrade_mode=latest: v6 SHA pin advances to v7 digest; # v6 comment → # v7."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}  # v6\n",
    )
    # _OLD tags as v6.0.2; _V7 is v7.0.1 (the newer major's release).
    tags_proc = _make_tags_proc(
        f"v7.0.1 {_V7}",
        f"v7 {_V7}",
        f"v6.0.2 {_OLD}",
        f"v6 {_OTHER}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(
            tmp_path,
            apply=True,
            keep_sha=True,
            refresh_sha=True,
            upgrade_mode="latest",
        )

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1, bumps
    b = checkout[0]
    assert b.pin == _OLD
    assert b.latest == _V7
    assert "v6" in (b.detail or "") and "v7" in (b.detail or "")
    assert "cross-major" in (b.detail or "").lower()

    text = wf.read_text(encoding="utf-8")
    assert _V7 in text, "new SHA not written"
    assert _OLD not in text, "old SHA still present"
    assert "# v7" in text, "comment not updated to v7"
    assert "# v6" not in text, "old comment still present"


def test_cross_major_refresh_normalize_stays_within_major(tmp_path: Path) -> None:
    """upgrade_mode=normalize (default): refresh stays within v6 even when v7 exists."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}  # v6\n",
    )
    tags_proc = _make_tags_proc(
        f"v7.0.1 {_V7}",
        f"v7 {_V7}",
        f"v6.0.3 {_NEW}",
        f"v6.0.2 {_OLD}",
        f"v6 {_OTHER}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(
            tmp_path,
            apply=True,
            keep_sha=True,
            refresh_sha=True,
            upgrade_mode="normalize",
        )

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    # Must advance within v6 (to v6.0.3), not jump to v7.
    assert b.latest == _NEW
    assert "v6.0.3" in (b.detail or "")
    assert "cross-major" not in (b.detail or "").lower()

    text = wf.read_text(encoding="utf-8")
    assert _NEW in text
    assert "# v6" in text  # comment stays at v6
    assert "# v7" not in text


def test_cross_major_refresh_no_newer_major_exact_tag_stays_within(
    tmp_path: Path,
) -> None:
    """Bare-major safety: v7 exists but only as bare tag (no v7.M.P) → stay in v6."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}  # v6\n",
    )
    # v7 has only a bare tag, no vN.M.P release — _highest_exact_digest_in_major returns None.
    tags_proc = _make_tags_proc(
        f"v7 {_V7}",
        f"v6.0.3 {_NEW}",
        f"v6.0.2 {_OLD}",
        f"v6 {_OTHER}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(
            tmp_path,
            apply=True,
            keep_sha=True,
            refresh_sha=True,
            upgrade_mode="latest",
        )

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    # Falls back to within-v6 refresh.
    assert b.latest == _NEW
    assert "v6.0.3" in (b.detail or "")
    assert "cross-major" not in (b.detail or "").lower()
    text = wf.read_text(encoding="utf-8")
    assert "# v6" in text
    assert "# v7" not in text


def test_cross_major_refresh_already_current_on_new_major_is_noop(
    tmp_path: Path,
) -> None:
    """Pin already at v7's digest → already_current short-circuit, report only."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_V7}  # v7\n",
    )
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(
        f"v7.0.1 {_V7}",
        f"v7 {_V7}",
        f"v6.0.2 {_OLD}",
        f"v6 {_OTHER}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(
            tmp_path,
            apply=True,
            keep_sha=True,
            refresh_sha=True,
            upgrade_mode="latest",
        )

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.detail == "SHA-pinned; report only"
    assert wf.read_text(encoding="utf-8") == original
