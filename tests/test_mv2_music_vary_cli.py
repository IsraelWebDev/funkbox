"""MV-2: ``funkbox music vary`` writes one score per variant, plus a manifest that records the seed and hashes."""

import hashlib
import json
import types

import pytest

from funkbox.music import tools as music_tools
from funkbox.music.vary import FIELDS, MANIFEST, generate, score_sha256, write_set

BRIEF = {
    "bpm_range": [96, 124],
    "keys": ["D", "A", "E", "G"],
    "scales": ["freygish", "minor-pentatonic", "dorian", "mixolydian"],
    "programs": [0, 24, 32, 40, 56, 71],
    "drum_patterns": ["straight", "half-time", "shuffle"],
    "swing": [0.0, 0.2],
    "arrangement": "build",
}
BASE = {
    "name": "base",
    "bpm": 100,
    "meter": 4,
    "key": "D",
    "scale": "minor",
    "bars": 4,
    "swing": 0.0,
    "tracks": [{"name": "pad", "program": 0, "octave": 4, "notes": "[^1 ^3 ^5]/1"}],
}


def _base_file(tmp_path, base=BASE, name="SCORE.music.json"):
    p = tmp_path / name
    p.write_text(json.dumps(base), encoding="utf-8")
    return p


def _brief_file(tmp_path, brief=BRIEF):
    p = tmp_path / "brief.json"
    p.write_text(json.dumps(brief), encoding="utf-8")
    return p


def _args(score, brief, **kw):
    d = dict(score=str(score), brief=str(brief), seed=7, count=3, out=None, force=False)
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_write_set_writes_one_file_per_variant_and_a_manifest(tmp_path):
    from funkbox.music.score import check

    out = tmp_path / "out"
    m = write_set(BRIEF, 7, 6, out, base=BASE, name="SCORE")
    names = sorted(p.name for p in out.glob("*.music.json"))
    assert names == [f"SCORE-{i:02d}.music.json" for i in range(1, 7)]
    assert (out / MANIFEST).is_file()
    assert len(m["variants"]) == 6
    for n in names:
        score = json.loads((out / n).read_text(encoding="utf-8"))
        assert not check(score)
        assert score["bars"] == BASE["bars"]
        assert BRIEF["bpm_range"][0] <= score["bpm"] <= BRIEF["bpm_range"][1]


def test_manifest_records_seed_brief_and_hashes(tmp_path):
    out = tmp_path / "out"
    m = write_set(BRIEF, 7, 3, out, base=BASE, name="SCORE")
    assert m["seed"] == 7 and m["count"] == 3 and m["bars"] == 4 and m["meter"] == 4
    assert m["brief"] == BRIEF
    assert len(m["brief_sha256"]) == 64
    assert json.loads((out / MANIFEST).read_text(encoding="utf-8")) == m
    for row in m["variants"]:
        score = json.loads((out / row["file"]).read_text(encoding="utf-8"))
        assert row["sha256"] == score_sha256(score)


def test_manifest_hash_matches_the_bytes_on_disk(tmp_path):
    out = tmp_path / "out"
    m = write_set(BRIEF, 7, 2, out, base=BASE, name="SCORE")
    for row in m["variants"]:
        data = (out / row["file"]).read_bytes()
        assert b"\r\n" not in data
        assert row["file_sha256"] == hashlib.sha256(data).hexdigest()
    assert b"\r\n" not in (out / MANIFEST).read_bytes()


def test_regenerating_from_the_manifest_gives_the_same_scores(tmp_path):
    out = tmp_path / "out"
    m = write_set(BRIEF, 11, 4, out, base=BASE, name="SCORE")
    again = generate(m["brief"], m["seed"], m["count"], bars=m["bars"], meter=m["meter"], name="SCORE")
    assert [score_sha256(s) for s in again] == [r["sha256"] for r in m["variants"]]


def test_fields_that_change_are_counted_not_only_bpm(tmp_path):
    m = write_set(BRIEF, 7, 6, tmp_path / "out", base=BASE, name="SCORE")
    spread = m["fields_changed"]
    assert set(spread) == set(FIELDS)
    assert spread["bpm"] > 1
    changing = [f for f, n in spread.items() if n > 1]
    assert len(changing) >= 5, spread


def test_a_second_run_refuses_to_overwrite_without_force(tmp_path):
    out = tmp_path / "out"
    write_set(BRIEF, 7, 3, out, base=BASE, name="SCORE")
    with pytest.raises(FileExistsError):
        write_set(BRIEF, 8, 3, out, base=BASE, name="SCORE")


def test_force_replaces_the_set_and_removes_extra_variants(tmp_path):
    out = tmp_path / "out"
    write_set(BRIEF, 7, 5, out, base=BASE, name="SCORE")
    write_set(BRIEF, 9, 3, out, base=BASE, name="SCORE", force=True)
    names = sorted(p.name for p in out.glob("*.music.json"))
    assert names == ["SCORE-01.music.json", "SCORE-02.music.json", "SCORE-03.music.json"]
    m = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    assert m["seed"] == 9 and len(m["variants"]) == 3


def test_a_library_name_with_dots_or_slashes_is_refused(tmp_path):
    from funkbox.music.vary import BriefError

    for bad in ("my.song", "../x", ""):
        with pytest.raises(BriefError, match="name"):
            write_set(BRIEF, 7, 2, tmp_path / "out", base=BASE, name=bad)


def test_cli_vary_writes_the_set_next_to_the_score(tmp_path, capsys):
    score = _base_file(tmp_path)
    assert music_tools.cmd_vary(_args(score, _brief_file(tmp_path))) == 0
    out = tmp_path / "SCORE-variants"
    assert (out / "SCORE-01.music.json").is_file()
    assert (out / MANIFEST).is_file()
    assert "manifest:" in capsys.readouterr().out


def test_cli_second_run_needs_force(tmp_path, capsys):
    score = _base_file(tmp_path)
    brief = _brief_file(tmp_path)
    assert music_tools.cmd_vary(_args(score, brief)) == 0
    assert music_tools.cmd_vary(_args(score, brief)) == 1
    assert "--force" in capsys.readouterr().out
    assert music_tools.cmd_vary(_args(score, brief, force=True)) == 0


def test_cli_vary_prints_every_brief_problem_and_exits_1(tmp_path, capsys):
    bad = dict(BRIEF, scales=["nope"], drum_patterns=["bogus"])
    rc = music_tools.cmd_vary(_args(_base_file(tmp_path), _brief_file(tmp_path, bad)))
    text = capsys.readouterr().out
    assert rc == 1
    assert "scales" in text and "drum_patterns" in text


def test_cli_vary_refuses_a_base_that_is_not_4_4(tmp_path, capsys):
    score = _base_file(tmp_path, base=dict(BASE, meter=3))
    assert music_tools.cmd_vary(_args(score, _brief_file(tmp_path))) == 1
    assert "meter" in capsys.readouterr().out


def test_cli_vary_refuses_a_bad_score_name(tmp_path, capsys):
    score = _base_file(tmp_path, name="bad name.music.json")
    assert music_tools.cmd_vary(_args(score, _brief_file(tmp_path))) == 1
    assert "bad name" in capsys.readouterr().out


def test_cli_vary_refuses_a_name_with_extra_dots(tmp_path, capsys):
    score = _base_file(tmp_path, name="my.song.music.json")
    assert music_tools.cmd_vary(_args(score, _brief_file(tmp_path))) == 1
    assert "name" in capsys.readouterr().out


def test_cli_vary_refuses_a_score_that_is_not_an_object(tmp_path, capsys):
    score = tmp_path / "SCORE.music.json"
    score.write_text("[1, 2]", encoding="utf-8")
    assert music_tools.cmd_vary(_args(score, _brief_file(tmp_path))) == 1
    assert "JSON object" in capsys.readouterr().out


def test_cli_out_pointing_at_the_base_score_is_an_error_not_a_traceback(tmp_path, capsys):
    score = _base_file(tmp_path)
    assert music_tools.cmd_vary(_args(score, _brief_file(tmp_path), out=str(score))) == 1
    assert "error" in capsys.readouterr().out