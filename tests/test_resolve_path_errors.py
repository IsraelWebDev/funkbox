"""A missing file names the folders it looked in and the FUNK_HOME setting, not just the bare path."""

import pytest

from funkbox.config import resolve_path


def test_missing_shader_names_the_folders_it_looked_in(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (home, tmp_path / "out", tmp_path / "in"):
        d.mkdir()
    monkeypatch.setenv("FUNK_HOME", str(home))
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path / "in"))
    with pytest.raises(FileNotFoundError) as e:
        resolve_path("shaders/glow.frag")
    msg = str(e.value)
    assert "shaders/glow.frag" in msg
    assert str(home / "shaders" / "glow.frag") in msg  # where it looked
    assert "FUNK_HOME" in msg  # what to check
