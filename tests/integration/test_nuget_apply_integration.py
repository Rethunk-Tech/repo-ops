"""Integration test: a real NuGet bump through nuget.org and ``dotnet restore``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from repo_ops_models import Workspace
from repo_ops_nuget.nuget import apply_nuget

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("dotnet") is None, reason="dotnet not on PATH"),
]

_PROJECT = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.0</TargetFramework>
    <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.1" />
  </ItemGroup>
</Project>
"""


def test_apply_bumps_the_pin_and_its_lock_file(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "Probe.csproj").write_text(_PROJECT)
    ws = Workspace(path=tmp_path, ecosystem="dotnet", manager="nuget", git_repo=tmp_path, label="")

    assert apply_nuget(ws) == []

    assert 'Version="13.0.1"' not in (tmp_path / "Probe.csproj").read_text()
    lock = json.loads((tmp_path / "packages.lock.json").read_text())
    resolved = lock["dependencies"][".NETStandard,Version=v2.0"]["Newtonsoft.Json"]["resolved"]
    assert resolved != "13.0.1"
