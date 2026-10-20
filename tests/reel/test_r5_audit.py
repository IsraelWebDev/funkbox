import json

import pytest

from conftest import make_plain_reel
from reel import render


def small_reel(demo, dur=2.5):
    d = make_plain_reel(demo)
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    for sh in d["shots"]:
        sh["duration"] = dur
    return d


def test_every_drawn_string_is_byte_identical_to_its_copy_entry(demo, gl, work, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    out = render.render_reel(small_reel(demo), "audited", base_dir=demo, out_dir=work, audit=True, with_audio=False)
    audit_path = work / "audited.text_audit.json"
    assert audit_path.exists() and out.paths["text_audit"] == str(audit_path)
    doc = json.loads(audit_path.read_text(encoding="utf-8"))
    deck = json.loads((demo / "inputs/brand/copy.json").read_text(encoding="utf-8"))["lines"]
    reel = json.loads((demo / "demo.reel.json").read_text())
    # every drawn string equals copy.lines[id].text, byte for byte
    assert doc["entries"]
    for e in doc["entries"]:
        want = deck[e["copy_id"]]["text"]
        assert e["text"].encode("utf-8") == want.encode("utf-8"), e
        assert e["text_exact"] and e["complete_frame"] is not None
        assert e["frames"][0] <= e["complete_frame"] <= e["frames"][1]
    # an entry for every caption and every end-card slot
    got = {(e["shot"], e["slot"], e["instance"]): e["copy_id"] for e in doc["entries"]}
    for sh in reel["shots"]:
        if "caption" in sh:
            assert got[(sh["id"], "caption", "main")] == sh["caption"]["copy_id"]
    ec = reel["end_card"]["copy"]
    assert got[("end_card", "headline", "main")] == ec["headline"]
    assert got[("end_card", "sub", "main")] == ec["sub"]
    assert got[("end_card", "url", "top")] == ec["url"] and got[("end_card", "url", "bottom")] == ec["url"]
    assert doc["ok"] and not doc["missing"] and not doc["inexact"] and not doc["never_fully_typed"]
    assert out.audit_ok is True


def test_audit_catches_text_that_differs_from_the_deck(demo, gl, work, monkeypatch):
    """If anything drew a string other than the deck's, the audit must say so (it compares glyph
    strings with the deck, not the deck with itself)."""
    monkeypatch.setenv("REEL_DEPTH", "stub")
    plan = render.prepare(small_reel(demo, 2.5), demo)
    r = render.Renderer(plan.reel, plan.deck, plan.spans, width=270, height=480, depth_mode="off")
    try:
        r.audit = {}
        span = r.span_for_shot("s3")
        r.caption_layout(span)                                   # layout made from the genuine text
        cid = plan.reel.shots[2].caption.copy_id
        from dataclasses import replace
        stt = r._state[span.index]
        lay = stt.extra["caption"]
        stt.extra["caption"] = replace(lay, text=lay.text.replace("GLOWMOP", "GLOW MOP"),
                                       lines=[(s, min(e, len(lay.text.replace("GLOWMOP", "GLOW MOP")))) for s, e in lay.lines])
        for k in range(0, 25):
            r.shot_frame(span, k)
        rep = r.audit_report()
        assert rep and not all(e["text_exact"] for e in rep)
        assert any(e["copy_id"] == cid and not e["text_exact"] for e in rep)
    finally:
        r.release()


def test_audit_reports_missing_entries(demo, gl, work, monkeypatch):
    monkeypatch.setenv("REEL_DEPTH", "stub")
    d = small_reel(demo, 0.5)           # too short for typewriters: captions start but never finish
    out = render.render_reel(d, "short", base_dir=demo, out_dir=work, audit=True, with_audio=False)
    doc = json.loads((work / "short.text_audit.json").read_text())
    assert doc["never_fully_typed"]
