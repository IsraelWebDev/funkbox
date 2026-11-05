import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cfg():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_build_system_is_setuptools():
    cfg = _cfg()
    assert cfg["build-system"]["build-backend"] == "setuptools.build_meta"
    assert any(r.startswith("setuptools") for r in cfg["build-system"]["requires"])


def test_packages_are_the_funkbox_package_only():
    find = _cfg()["tool"]["setuptools"]["packages"]["find"]
    assert find["include"] == ["funkbox", "funkbox.*"]


def test_launcher_module_is_not_packaged():
    assert "funkbox" not in _cfg()["tool"]["setuptools"].get("py-modules", [])
