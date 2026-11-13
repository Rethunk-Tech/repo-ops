from __future__ import annotations

from repo_ops_core.requirement import requirement_satisfied


def test_caret_default_treats_bare_version_as_range() -> None:
    assert requirement_satisfied("2.31.0", "2.28.0", default_operator="^") is True
    assert requirement_satisfied("3.0.0", "2.28.0", default_operator="^") is False
    assert requirement_satisfied("2.31.0", "==2.28.0", default_operator="^") is False


def test_exact_default_treats_bare_version_as_pin() -> None:
    assert requirement_satisfied("2.31.0", "2.31.0", default_operator="==") is True
    assert requirement_satisfied("2.32.0", "2.31.0", default_operator="==") is False
    assert requirement_satisfied("3.0.0", ">=2.31.0", default_operator="==") is True


def test_explicit_caret_is_independent_of_default() -> None:
    assert requirement_satisfied("2.31.0", "^2.28.0", default_operator="==") is True
    assert requirement_satisfied("3.0.0", "^2.28.0", default_operator="==") is False


def test_wildcard_and_empty_requirements_match() -> None:
    assert requirement_satisfied("1.0.0", "*", default_operator="^") is True
    assert requirement_satisfied("1.0.0", "", default_operator="==") is True


def test_pep440_compatible_release_uses_all_but_last_digit() -> None:
    assert requirement_satisfied("2.32.0", "~=2.31", default_operator="==") is True
    assert requirement_satisfied("3.0.0", "~=2.31", default_operator="==") is False


def test_poetry_tilde_one_component_allows_minor() -> None:
    assert requirement_satisfied("1.9.0", "~1", default_operator="^") is True
    assert requirement_satisfied("2.0.0", "~1", default_operator="^") is False
