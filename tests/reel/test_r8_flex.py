"""v4.1 flexible reels: framing (crop + scale only) on video and stills, speed ramps, a fixed BPM grid, several
output formats from one edit, landscape captions."""
import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from funkbox.media import require_ffmpeg
from funkbox.reel import framing, timeline
from funkbox.reel.schema import FrameKey, ReelError, materialize, parse_reel

MARK = (20, 200, 90)                      # the "ACCEPT button": a green box at x 0.7..0.9, y 0.55..0.65
MARK_RECT = [0.7, 0.55, 0.2, 0.1]


def _frame(i: int, w: int = 320, h: int = 180) -> np.ndarray:
    """Grey level encodes the frame index (i * 4), plus the green mark."""
    a = np.full((h, w, 3), min(255, i * 4), np.uint8)
    a[int(0.55 * h):int(0.65 * h), int(0.7 * w):int(0.9 * w)] = MARK
    return a


@pytest.fixture(scope="module")
def rec(tmp_path_factory, gl):
    from funkbox.config import funk_out
    d = funk_out() / "test_flex"
    d.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([require_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", "320x180", "-r", "30", "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p",
                          str(d / "rec.mp4")], stdin=subprocess.PIPE)
    for i in range(60):
        p.stdin.write(_frame(i).tobytes())
    p.stdin.close()
    p.wait()
    Image.fromarray(_frame(30)).save(d / "shot.png")
    (d / "copy.json").write_text(json.dumps({"lines": {"hook": {"text": "ONE TAP TO ACCEPT"}}}))
    return d


def reel(**over):
    d = {"version": 3, "settings": {"width": 180, "height": 320, "fps": 30, "safe_area": "reels"},
         "copy": "copy.json", "audio": {"bpm": 120},
         "shots": [{"id": "push", "source": {"video": "rec.mp4"}, "duration": "beats:2",
                    "motion": {"type": "frame", "to": MARK_RECT}}]}
    d.update(over)
    return d


# ---------------------------------------------------------------- framing maths
def test_window_has_the_output_aspect_and_contains_the_rect():
    w = framing.window((0.7, 0.55, 0.2, 0.1), 1920, 1080, 1080, 1920)
    assert abs((w.x1 - w.x0) / (w.y1 - w.y0) - 1080 / 1920) < 1e-6
    e = 1e-6
    assert w.x0 <= 0.7 * 1920 + e and w.x1 >= 0.9 * 1920 - e and w.y0 <= 0.55 * 1080 + e and w.y1 >= 0.65 * 1080 - e
    assert w.cut == 0 and w.inside


def test_cover_shrinks_a_too_wide_rect_and_contain_pads_instead():
    wide = (0.0, 0.4, 1.0, 0.2)                     # a full-width strip of a 16:9 source, into 9:16
    c = framing.window(wide, 1920, 1080, 1080, 1920, fit="cover")
    assert c.cut > 0.5 and c.x0 >= 0 and c.x1 <= 1920 and c.y0 >= 0 and c.y1 <= 1080
    p = framing.window(wide, 1920, 1080, 1080, 1920, fit="contain")
    assert p.cut == 0 and p.y0 < 0 and p.y1 > 1080 and (p.x0, p.x1) == (0, 1920)


def test_focus_moves_the_default_crop():
    left = framing.window(framing.FULL, 1920, 1080, 1080, 1920, centre=(0.1, 0.5))
    assert left.x0 == 0                             # clamped against the left edge
    mid = framing.window(framing.FULL, 1920, 1080, 1080, 1920)
    assert abs((mid.x0 + mid.x1) / 2 - 960) < 1e-6


def test_rect_at_eases_and_scales_geometrically():
    keys = (FrameKey((0, 0, 1, 1), t=0.0), FrameKey((0.4, 0.4, 0.25, 0.25), t=1.0, ease="linear"))
    assert framing.rect_at(keys, 0, 2) == (0, 0, 1, 1) and framing.rect_at(keys, 1, 2) == (0.4, 0.4, 0.25, 0.25)
    assert abs(framing.rect_at(keys, 0.5, 2)[2] - 0.5) < 1e-9          # sqrt(1 * 0.25)
    secs = (FrameKey((0, 0, 1, 1), s=0.0), FrameKey((0.4, 0.4, 0.2, 0.2), s=1.0, ease="hold"))
    assert framing.rect_at(secs, 0.25, 2)[2] == 1 and framing.rect_at(secs, 0.5, 2)[2] == pytest.approx(0.2)


def test_speed_ramp_integrates():
    assert framing.source_time(2.0, 2.0, (1.0, 1.0)) == 2.0
    assert framing.source_time(2.0, 2.0, (1.0, 3.0)) == pytest.approx(4.0)          # mean speed 2
    assert framing.source_time(1.0, 2.0, (2.0, 2.0)) == 2.0
    assert framing.decode_fps(30, (1.0, 2.5)) == 90


# ---------------------------------------------------------------- schema
@pytest.mark.parametrize("patch, needle", [
    ({"motion": {"type": "frame"}}, "needs `to`"),
    ({"motion": {"type": "frame", "to": [0.9, 0.5, 0.2, 0.1]}}, "inside 0..1"),
    ({"motion": {"type": "kenburns", "to": [0.1, 0.1, 0.2, 0.2]}}, "belong to motion type"),
    ({"motion": {"type": "parallax"}}, "parallax needs a still image"),
    ({"source": {"video": "rec.mp4", "speed": 9}}, "between 0.1 and 8"),
    ({"source": {"image": "shot.png", "speed": 2}}, "only a video source"),
    ({"source": {"video": "rec.mp4", "pad": "smear"}}, "blur"),
    ({"motion": {"type": "frame", "keys": [{"rect": [0, 0, 1, 1], "t": 0.5}, {"rect": [0, 0, 1, 1], "t": 0.2}]}},
     "must not decrease"),
    ({"formats": {"16x9": {"duration": 2}}}, "can override only"),
    ({"formats": {"4x5": {"caption": {"anchor": "top"}}}}, "no such format"),
])
def test_schema_rejects_bad_flex_fields(patch, needle):
    d = reel(formats=["9x16", "16x9"])
    d["shots"][0].update(patch)
    with pytest.raises(ReelError) as e:
        parse_reel(d)
    assert any(needle in m for m in e.value.errors), e.value.errors


def test_beats_halves_and_offset_needs_bpm():
    d = reel()
    d["shots"][0]["duration"] = "beats:1.5"
    assert parse_reel(d).shots[0].duration == "beats:1.5"
    d["audio"] = {"beat_offset": 0.2}
    with pytest.raises(ReelError, match="needs audio.bpm"):
        parse_reel(d)


def test_materialize_applies_per_format_overrides():
    d = reel(formats=["9x16", {"id": "wide", "width": 640, "height": 360}])
    d["shots"][0]["caption"] = {"copy_id": "hook", "anchor": "top"}
    d["shots"][0]["formats"] = {"wide": {"caption": {"anchor": "bottom"},
                                          "motion": {"type": "frame", "to": [0.5, 0.5, 0.4, 0.4]}}}
    w = materialize(d, "wide")
    assert w["settings"]["width"] == 640 and w["settings"]["safe_area"] == "none"
    assert w["shots"][0]["caption"] == {"copy_id": "hook", "anchor": "bottom"}
    assert w["shots"][0]["motion"]["to"] == [0.5, 0.5, 0.4, 0.4] and "formats" not in w and "formats" not in w["shots"][0]
    assert materialize(d, "9x16")["shots"][0]["caption"]["anchor"] == "top"
    with pytest.raises(ReelError):
        materialize(d, "1x1")


def test_fixed_bpm_grid_needs_no_music():
    r = parse_reel(reel())
    from funkbox.reel import audio
    spans = timeline.build_timeline(r, {}, audio.beat_grid_for(r))
    assert spans[0].duration == pytest.approx(1.0, abs=1 / 30)          # 2 beats at 120 BPM
    assert audio.fixed_grid(110)[1] == pytest.approx(0.5455, abs=1e-4)


# ---------------------------------------------------------------- rendering
def _still(d, base, shot="push", t=0.0, fmt=None):
    from funkbox.reel import render
    plan = render.prepare(d, base, fmt)
    st = plan.reel.settings
    r = render.Renderer(plan.reel, plan.deck, plan.spans, width=st.width, height=st.height)
    try:
        return r.still(shot, t)
    finally:
        r.release()


def _green(fr) -> float:
    return float((np.abs(fr.astype(int) - MARK).sum(-1) < 60).mean())


def test_video_push_in_ends_on_the_rect_in_every_format(rec, gl):
    d = reel(formats=[{"id": "tall", "width": 180, "height": 320}, {"id": "wide", "width": 320, "height": 180}])
    for fmt, (W, H) in (("tall", (180, 320)), ("wide", (320, 180))):
        a = _still(d, rec, t=0.0, fmt=fmt)
        b = _still(d, rec, t=0.99, fmt=fmt)
        win = framing.window(tuple(MARK_RECT), 320, 180, W, H)        # where the push-in ends
        expect = (0.2 * 320 * 0.1 * 180) / ((win.x1 - win.x0) * (win.y1 - win.y0))
        assert abs(_green(b) - expect) < 0.05 and _green(b) > 3 * _green(a), (fmt, _green(a), _green(b), expect)
    assert _still(d, rec, t=0.5, fmt="wide").shape == (180, 320, 3)


def test_speed_ramp_plays_the_right_source_frame(rec, gl):
    d = reel()
    d["shots"][0] = {"id": "push", "source": {"video": "rec.mp4", "speed": [1, 3]}, "duration": 1.0,
                     "motion": {"type": "none"}}
    fr = _still(d, rec, t=29 / 30)                    # 29/30 s into a 1 s shot ramping 1->3: ~1.9 s of source
    grey = float(np.median(fr[:20, :20, 0]))
    expect = framing.source_time(29 / 30, 1.0, (1, 3)) * 30 * 4
    assert abs(grey - expect) <= 10, (grey, expect)


def test_contain_with_blur_pad_keeps_the_whole_frame(rec, gl):
    d = reel()
    d["shots"][0] = {"id": "push", "source": {"image": "shot.png", "fit": "contain", "pad": "blur"},
                     "duration": 1.0, "motion": {"type": "none"}}
    fr = _still(d, rec)
    band = fr[:40].astype(int)                         # 9:16 frame, 16:9 source: padded above and below
    assert band.std() < 30 and 0 < band.mean() < 255
    assert _green(fr) > 0.003                          # the whole source, mark included, is visible


def test_landscape_safe_area_and_caption_fit(rec, gl):
    from funkbox.reel.validate import validate
    d = reel(formats=["9x16", "16x9"])
    d["settings"] = {"width": 1080, "height": 1920, "fps": 30}
    d["shots"][0]["caption"] = {"copy_id": "hook", "style": "red-box", "anchor": "bottom"}
    rep = validate(d, rec)
    assert rep.ok, rep.errors
    assert "16x9 1920x1080" in rep.info["summary"]


def test_framing_warning_names_the_shot(rec, gl):
    from funkbox.reel.validate import validate
    d = reel()
    d["shots"][0]["motion"] = {"type": "frame", "to": [0.0, 0.4, 1.0, 0.2]}    # a full-width strip into 9:16
    rep = validate(d, rec)
    assert rep.ok and any("shot push" in w and "cut off" in w for w in rep.warnings), rep.warnings


def test_render_formats_writes_one_file_per_format(rec, gl, work):
    from funkbox.reel import render
    d = reel(formats=[{"id": "tall", "width": 90, "height": 160}, {"id": "wide", "width": 160, "height": 90}])
    outs = render.render_formats(d, "flex", base_dir=rec, out_dir=work, audit=True)
    assert [(o.width, o.height) for o in outs] == [(90, 160), (160, 90)]
    assert all(o.audit_ok is not False for o in outs)
    assert (work / "flex-tall.mp4").is_file() and (work / "flex-wide.mp4").is_file()
