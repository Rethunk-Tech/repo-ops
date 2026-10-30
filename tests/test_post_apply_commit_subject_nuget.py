"""Commit subjects for a NuGet-only apply."""

from repo_ops_upgrade.post_apply_commit import classify_commit_subject


def test_nuget_files_name_the_ecosystem() -> None:
    assert classify_commit_subject(["global.json"]) == "chore(deps): update NuGet packages"
    assert (
        classify_commit_subject(["src/Mod/Mod.csproj", "src/Mod/packages.lock.json"])
        == "chore(deps): update NuGet packages"
    )
