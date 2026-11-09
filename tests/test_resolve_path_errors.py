"""A missing built-in shader names FUNK_HOME, the folder that holds shaders/."""

import pytest

from funkbox.gl.recipe import Layer, layer_source


def test_missing_shader_names_FUNK_HOME(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (home, tmp_path / "out", tmp_path / "in"):
        d.mkdir()
    monkeypatch.setenv("FUNK_HOME", str(home))
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path / "in"))
    with pytest.raises(FileNotFoundError, match="FUNK_HOME") as e:
        layer_source(Layer(type="shader", shader="shaders/glow.frag"), None)
    assert str(home) in str(e.value)
