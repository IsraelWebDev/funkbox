"""Beat audit: every cut's distance from the beat grid, saved next to the text audit."""

import json
from types import SimpleNamespace

import pytest

from conftest import make_plain_reel
from funkbox.reel import audio, beataudit, timeline
from funkbox.reel.schema import parse_reel


def snap_reel(durs, snap=True, fps=30, bpm=104):
    d = {
        "copy": "c.json",
        "settings": {"fps": fps},
        "shots": [{"id": f"s{i}", "source": {"color": "#112233"}, "duration": x} for i, x in enumerate(durs)],
        "end_card": {"template": "brand-split", "duration": 2.0, "copy": {"headline": "a"}},
        "audio": {"voiceover": False, "snap_cuts_to_beats": snap, "bpm": bpm},
    }
    reel = parse_reel(d)
    beats = audio.beat_grid_for(reel)
    return reel, timeline.build_timeline(reel, {}, beats), beats


def test_snapped_cuts_are_within_half_a_frame_and_the_offsets_are_signed():
    reel, spans, beats = snap_reel([1.1, 1.4, 0.9])  # 104 BPM: a beat is 0.577 s, never a whole frame at 30 fps
    doc = beataudit.beat_audit(reel, spans, 30, beats)
    assert (doc["bpm"], doc["grid"], doc["fps"], doc["threshold_ms"]) == (104.0, "fixed", 30, 30.0)
    assert [c["cut"] for c in doc["cuts"]] == ["s1", "s2", "end_card"]  # every span start after the first
    assert doc["ok"] and doc["over"] == []
    for c, sp in zip(doc["cuts"], spans[1:]):
        assert c["frame"] == sp.start_frame and c["time_s"] == pytest.approx(sp.start, abs=1e-4)
        assert c["offset_ms"] == pytest.approx((c["time_s"] - c["beat_s"]) * 1000, abs=0.11)  # + = after the beat
        assert abs(c["offset_ms"]) <= doc["frame_ms"] / 2 + 0.1 and c["ok"]
    assert doc["max_offset_ms"] == max(abs(c["offset_ms"]) for c in doc["cuts"]) > 0
    assert any(c["offset_ms"] < 0 for c in doc["cuts"]) and any(c["offset_ms"] > 0 for c in doc["cuts"])


def test_a_cut_far_from_the_beat_fails_the_audit_and_is_named():
    reel, spans, beats = snap_reel([1.0, 1.0], snap=False)  # not snapped: the cut at 1.0 s is 154 ms before a beat
    doc = beataudit.beat_audit(reel, spans, 30, beats)
    first = doc["cuts"][0]
    assert first["cut"] == "s1" and first["offset_ms"] == pytest.approx(-153.8, abs=0.1) and not first["ok"]
    assert not doc["ok"] and "s1" in doc["over"] and doc["max_offset_ms"] >= 153.8
    assert beataudit.beat_audit(reel, spans, 30, beats, max_ms=500)["ok"]  # the threshold is the only judge


def test_the_audit_uses_the_frame_rate_of_the_render():
    reel, spans, beats = snap_reel([1.1, 1.4, 0.9])
    draft = [  # what Renderer does to the spans for a 24 fps draft
        SimpleNamespace(id=s.id, start_frame=round(s.start_frame * 24 / 30), end_frame=round(s.end_frame * 24 / 30))
        for s in spans
    ]
    doc = beataudit.beat_audit(reel, draft, 24, beats)
    assert doc["frame_ms"] == 41.7
    assert [c["time_s"] for c in doc["cuts"]] == [round(s.start_frame / 24, 4) for s in draft[1:]]


def test_grid_source_follows_the_order_the_grid_is_made_in():
    r = SimpleNamespace(audio=SimpleNamespace(bpm=None, music=None))
    assert beataudit.grid_source(r) == "detected"
    r.audio.music = "music/track.mp3"
    assert beataudit.grid_source(r) == "detected"
    r.audio.music = "music/day.music.json"
    assert beataudit.grid_source(r) == "score"
    r.audio.bpm = 120
    assert beataudit.grid_source(r) == "fixed"


def test_the_file_is_written_only_when_cuts_snap_and_a_stale_one_is_removed(tmp_path):
    reel, spans, _ = snap_reel([1.1, 1.4, 0.9])
    rep = beataudit.write_beat_audit(reel, spans, 30, "final", tmp_path)
    path = tmp_path / "final.beat_audit.json"
    assert rep == {"path": str(path), "ok": True}
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["reel"] == "final" and doc["ok"] and len(doc["cuts"]) == 3

    plain, plain_spans, _ = snap_reel([1.1, 1.4, 0.9], snap=False)
    assert beataudit.write_beat_audit(plain, plain_spans, 30, "final", tmp_path) is None
    assert not path.exists()  # the earlier render's audit must not outlive a reel that no longer snaps


def test_the_render_report_line():
    assert beataudit.report_line(SimpleNamespace()) is None  # an output without the field: no line
    assert beataudit.report_line(SimpleNamespace(beat_ok=None)) is None
    assert beataudit.report_line(SimpleNamespace(beat_ok=True)) == "beat audit: OK, every cut is within 30 ms of a beat"
    assert "more than 30 ms" in beataudit.report_line(SimpleNamespace(beat_ok=False))


def beat_reel(demo, snap=True):
    d = make_plain_reel(demo)
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    d["audio"] = {"voiceover": False, "bpm": 120, "snap_cuts_to_beats": snap}
    for sh in d["shots"]:
        sh["duration"] = 2.6  # off the 0.5 s grid, so the cuts have to move
    return d


def test_a_render_with_snapped_cuts_writes_the_beat_audit(demo, gl, work, monkeypatch):
    from funkbox.reel import render

    monkeypatch.setenv("REEL_DEPTH", "stub")
    out = render.render_reel(beat_reel(demo), "beat", base_dir=demo, out_dir=work, audit=True, with_audio=False)
    path = work / "beat.beat_audit.json"
    assert out.paths["beat_audit"] == str(path) and out.beat_ok is True
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["reel"] == "beat" and doc["bpm"] == 120.0 and doc["grid"] == "fixed" and doc["fps"] == 10
    plan = render.prepare(beat_reel(demo), demo)
    assert [c["frame"] for c in doc["cuts"]] == [sp.start_frame for sp in plan.spans[1:]]
    assert all(c["offset_ms"] == 0 for c in doc["cuts"])  # 120 BPM at 10 fps: every beat is a whole frame


def test_a_render_without_snapping_writes_no_beat_audit(demo, gl, work, monkeypatch):
    from funkbox.reel import render

    monkeypatch.setenv("REEL_DEPTH", "stub")
    out = render.render_reel(
        beat_reel(demo, snap=False), "nobeat", base_dir=demo, out_dir=work, audit=True, with_audio=False
    )
    assert "beat_audit" not in out.paths and out.beat_ok is None
    assert not (work / "nobeat.beat_audit.json").exists() and (work / "nobeat.text_audit.json").exists()
