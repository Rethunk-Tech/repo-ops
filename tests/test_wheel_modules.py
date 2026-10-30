"""Every src package ships in the wheel; uv's build backend only packs the names listed."""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_every_src_package_is_a_wheel_module() -> None:
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    listed = set(config["tool"]["uv"]["build-backend"]["module-name"])
    on_disk = {p.parent.name for p in (_ROOT / "src").glob("*/__init__.py")}
    assert on_disk == listed
