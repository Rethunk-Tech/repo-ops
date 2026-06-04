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


def test_normalize_version_strips_leading_v():
    assert normalize_version("v4.1.0") == "4.1.0"
    assert normalize_version("v10.0.0") == "10.0.0"


def test_semver_tuple_v_prefixed():
    # v4.1.0 must parse as (4,1,0), not (0,1,0)
    assert semver_tuple("v4.1.0") == (4, 1, 0)
    assert semver_tuple("v4.1.0") == semver_tuple("4.1.0")
    # ordering must hold across major versions
    assert semver_tuple("v10.0.0") > semver_tuple("v9.0.0")


def test_semver_tuple_uppercase_V_prefix():
    # V1.2.3 must parse as (1,2,3), not (0,2,3) — closes #58
    assert semver_tuple("V1.2.3") == (1, 2, 3)
    assert normalize_version("V1.2.3") == "1.2.3"
