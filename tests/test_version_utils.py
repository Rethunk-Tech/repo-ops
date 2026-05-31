"""Tests for pure version utilities."""

from repo_ops_core.version_utils import normalize_version, semver_tuple


def test_normalize_version_strips_prefixes_and_prerelease():
    assert normalize_version("^1.2.3") == "1.2.3"
    assert normalize_version("~>=2.0.0-beta") == "2.0.0"
    assert normalize_version("1.0") == "1.0"


def test_semver_tuple_parses_and_zero_fills():
    assert semver_tuple("1.2.3") == (1, 2, 3)
    assert semver_tuple("^2.0") == (2, 0)
    assert semver_tuple("foo") == (0,)
    assert semver_tuple("") == (0,)
