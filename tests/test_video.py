"""Video tools: probe, scene cuts, trim, burned-in subtitles with exact text."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from funkbox.media import require_ffmpeg
from funkbox.video import clip


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


def make_clip(
    path, colours=((220, 30, 40), (245, 245, 245), (20, 20, 120)), each=1.0, size="320x180", fps=25, audio=True
):
    """Solid colour shots of ``each`` seconds: cuts at 1.0 and 2.0 s."""
    ins, fil = [], []
    for i, c in enumerate(colours):
        ins += ["-f", "lavfi", "-i", f"color=c=0x{c[0]:02x}{c[1]:02x}{c[2]:02x}:s={size}:r={fps}:d={each}"]
        fil.append(f"[{i}:v]")
    n = len(colours)
    args = [require_ffmpeg(), "-hide_banner", "-loglevel", "error", *ins]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={each * n}"]
    graph = "".join(fil) + f"concat=n={n}:v=1:a=0[v]"
    args += ["-filter_complex", graph, "-map", "[v]"] + (["-map", f"{n}:a", "-c:a", "aac"] if audio else [])
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "12", "-y", str(path)]
    subprocess.run(args, check=True)
    return path


def test_info(work):
    p = make_clip(work / "c.mp4")
    i = clip.info(p)
    assert (i.width, i.height, i.fps, i.audio, i.codec) == (320, 180, 25.0, True, "h264")
    assert i.duration == pytest.approx(3.0, abs=0.1)
    assert not clip.info(make_clip(work / "s.mp4", audio=False)).audio
    (work / "x.mp4").write_bytes(b"not a video")
    with pytest.raises(clip.VideoError, match="not a video"):
        clip.info(work / "x.mp4")


def test_scenes_find_the_cuts_and_give_reel_sources(work):
    p = make_clip(work / "c.mp4")
    res = clip.scenes(p)
    assert [round(a, 1) for a, _ in res.shots] == [0.0, 1.0, 2.0]
    assert res.sources()[1] == {"video": "c.mp4", "trim": [1.0, 2.0]}
    files = clip.write_scenes(res)
    data = json.loads(files["scenes"].read_text())
    assert len(data["sources"]) == 3 and files["sheet"].exists()
    sheet = np.asarray(res.sheet)
    assert sheet[40, 120, 0] > 180 and sheet[40, 120, 1] < 80 and sheet[40, 360].min() > 200  # red, then white
    merged = clip.scenes(p, min_shot=1.5, sheet=False)  # too-short shots merge
    assert len(merged.shots) == 1
    with pytest.raises(clip.VideoError, match="threshold"):
        clip.scenes(p, threshold=2)


def test_trim_accurate_and_never_overwrites(work):
    p = make_clip(work / "c.mp4")
    t = clip.trim(p, 1.2, "0:02.0")
    i = clip.info(t)
    assert i.duration == pytest.approx(0.8, abs=0.08) and i.audio
    first = np.asarray(clip.frame_at(t, 0.05))
    assert first.mean() > 200  # starts inside the white shot
    t2 = clip.trim(p, 1.2, 2.0)
    assert t2 != t and t2.name.endswith("-2.mp4") and p.stat().st_size > 0
    fast = clip.trim(p, 0.0, duration=1.0, accurate=False, name="fast")
    assert clip.info(fast).duration == pytest.approx(1.0, abs=0.2)
    for bad in ((2.0, 1.0), (9.0, None)):
        with pytest.raises(clip.VideoError):
            clip.trim(p, *bad)


def test_srt_parsing():
    srt = "1\n00:00:00,500 --> 00:00:01,500\nHello <i>there</i>\n\n2\n00:00:02,000 --> 00:00:02,900\nצבאות ה׳\nלישראל\n"
    cs = clip.parse_srt(srt)
    assert [(c.start, c.end) for c in cs] == [(0.5, 1.5), (2.0, 2.9)]
    assert cs[0].text == "Hello <i>there</i>" and cs[1].text == "צבאות ה׳\nלישראל"
    vtt = "WEBVTT\n\n00:01.000 --> 00:02.000 align:center\nHi\n"
    assert clip.parse_srt(vtt)[0].start == 1.0


def test_subtitles_are_burned_in_only_during_their_cue(work):
    p = make_clip(work / "c.mp4", colours=((40, 40, 40),) * 3, size="640x360")
    res = clip.subtitles(
        p,
        cues=[{"start": 0.5, "end": 1.5, "text": "EXACT WORDS"}, {"start": 2.0, "end": 2.8, "text": "שלום עולם"}],
        style="red-box",
    )
    audit = json.loads(res.audit.read_text(encoding="utf-8"))
    assert [a["text"] for a in audit] == ["EXACT WORDS", "שלום עולם"] and all(a["text_exact"] for a in audit)

    def red(t):
        a = np.asarray(clip.frame_at(res.path, t, 640)).astype(int)
        return ((a[..., 0] > 150) & (a[..., 1] < 80)).sum()

    assert red(0.2) == 0 and red(1.0) > 500 and red(1.8) == 0 and red(2.4) > 300
    assert clip.info(res.path).audio
    with pytest.raises(clip.VideoError, match="style"):
        clip.subtitles(p, cues=[{"start": 0, "end": 1, "text": "x"}], style="comic-sans")
    with pytest.raises(clip.VideoError, match="before end"):
        clip.subtitles(p, cues=[{"start": 2, "end": 1, "text": "x"}])


def test_contact_sheet_has_one_labelled_frame_per_interval(work):
    p = make_clip(work / "c.mp4")  # 3 s, cuts at 1 s and 2 s
    res = clip.contact_sheet(p, every_s=1.0, cols=3, tw=160)
    assert res.times == [0.0, 1.0, 2.0]
    assert res.image.width == 3 * 160
    assert res.image.height == 1 * (round(160 * 180 / 320) + 18)  # one row of frames plus label strips
    assert res.path.exists() and res.path.name == "c-contact.jpg"


def test_contact_sheet_refuses_a_bad_interval(work):
    p = make_clip(work / "c.mp4")
    with pytest.raises(clip.VideoError, match="every_s"):
        clip.contact_sheet(p, every_s=0)
    with pytest.raises(clip.VideoError, match="every_s"):
        clip.contact_sheet(p, every_s=-1)


def test_contact_sheet_never_overwrites(work):
    p = make_clip(work / "c.mp4")
    first = clip.contact_sheet(p, every_s=1.0).path
    second = clip.contact_sheet(p, every_s=1.0).path
    assert first != second and first.exists() and second.exists()


def test_import_copies_clips_and_lists_what_it_took(work):
    cam = work / "camera"
    cam.mkdir()
    make_clip(cam / "IMG_1.MOV")
    make_clip(cam / "b.mp4", colours=((10, 200, 10),) * 2)
    (cam / "notes.txt").write_text("not a clip")
    dest = work / "in"
    res = clip.import_clips(cam, dest=dest)
    assert sorted(p.name for p in res.copied) == ["IMG_1.MOV", "b.mp4"]
    assert res.skipped == [] and res.dest == dest
    assert clip.info(dest / "IMG_1.MOV").duration == pytest.approx(3.0, abs=0.1)
    assert "2 copied" in res.text()


def test_import_never_overwrites(work):
    cam = work / "camera"
    cam.mkdir()
    make_clip(cam / "a.mp4")
    dest = work / "in"
    dest.mkdir()
    (dest / "a.mp4").write_bytes(b"keep me")
    res = clip.import_clips(cam, dest=dest)
    assert res.copied == [] and [p.name for p in res.skipped] == ["a.mp4"]
    assert (dest / "a.mp4").read_bytes() == b"keep me"


def test_import_refuses_a_file_an_empty_folder_and_outside_paths(work):
    cam = work / "camera"
    cam.mkdir()
    make_clip(cam / "x.txt.mp4")
    with pytest.raises(clip.VideoError, match="not a folder"):
        clip.import_clips(cam / "x.txt.mp4", dest=work / "in")
    empty = work / "empty"
    empty.mkdir()
    with pytest.raises(clip.VideoError, match="no video"):
        clip.import_clips(empty, dest=work / "in")
    with pytest.raises(ValueError):
        clip.import_clips("/etc", dest=work / "in")


def _edit_clips(work):
    red = make_clip(work / "red.mp4", colours=((220, 30, 40),), each=2.0)
    blue = make_clip(work / "blue.mp4", colours=((20, 20, 120),), each=2.0)
    return red, blue


def _centre(img):
    a = np.asarray(img).astype(int)
    return a[a.shape[0] // 2, a.shape[1] // 2]


def test_edit_joins_cuts_with_a_white_flash_padded_to_9_16(work):
    red, blue = _edit_clips(work)
    cuts = [{"path": str(red), "start": 0.5, "end": 1.5}, {"path": str(blue), "start": 0.0, "end": 1.0}]
    res = clip.edit(cuts, transition="flash", grade="none", name="flash")
    i = clip.info(res.path)
    assert (i.width, i.height) == (720, 1280) and i.audio
    assert i.duration == pytest.approx(1.75, abs=0.1)  # 1 s + 1 s, minus the 0.25 s flash overlap
    assert clip.frame_at(res.path, 0.1, 360).getpixel((180, 5))[:3] == (0, 0, 0)  # pillarbox bars
    assert _centre(clip.frame_at(res.path, 0.3, 360))[0] > 150  # red first cut
    flash = [_centre(clip.frame_at(res.path, t, 360)).min() for t in np.arange(0.75, 1.0, 0.03)]
    assert max(flash) > 230  # the white flash is in the join
    assert _centre(clip.frame_at(res.path, 1.5, 360))[2] > 90  # blue second cut


def test_edit_cut_mode_has_no_flash_and_is_exactly_the_sum(work):
    red, blue = _edit_clips(work)
    cuts = [{"path": str(red), "start": 0.5, "end": 1.5}, {"path": str(blue), "start": 0.0, "end": 1.0}]
    res = clip.edit(cuts, transition="cut", grade="none", name="cut")
    assert clip.info(res.path).duration == pytest.approx(2.0, abs=0.1)
    before, after = _centre(clip.frame_at(res.path, 0.95, 360)), _centre(clip.frame_at(res.path, 1.05, 360))
    assert before[0] > 150 and before[2] < 90 and after[2] > 90 and after.min() < 120


def test_edit_funky_grade_pushes_colour_and_none_leaves_it(work):
    red, _ = _edit_clips(work)
    cuts = [{"path": str(red), "start": 0.5, "end": 1.5}]
    plain = clip.edit(cuts, transition="cut", grade="none", name="plain")
    funky = clip.edit(cuts, transition="cut", grade="funky", name="funky")
    p, f = _centre(clip.frame_at(plain.path, 0.5, 360)), _centre(clip.frame_at(funky.path, 0.5, 360))
    assert (f[0] - f[1]) > (p[0] - p[1]) + 10  # saturation up: the red is redder


def test_edit_aspect_16_9_and_silent_sources_still_get_audio(work):
    silent = make_clip(work / "silent.mp4", colours=((90, 90, 90),), each=1.5, audio=False)
    res = clip.edit([{"path": str(silent), "start": 0, "end": 1.0}], transition="cut", grade="none", aspect="16:9")
    i = clip.info(res.path)
    assert (i.width, i.height) == (1280, 720) and i.audio


def test_edit_size_target_is_aimed_at(work):
    red, blue = _edit_clips(work)
    cuts = [{"path": str(red), "start": 0.0, "end": 2.0}, {"path": str(blue), "start": 0.0, "end": 2.0}]
    res = clip.edit(cuts, transition="cut", grade="funky", target_mb=0.4, name="small")
    assert res.path.stat().st_size <= 0.4 * 1024 * 1024 * 1.15
    with pytest.raises(clip.VideoError, match="too small"):
        clip.edit(cuts, transition="cut", grade="none", target_mb=0.001)


def test_edit_refuses_bad_input_and_never_overwrites(work):
    red, blue = _edit_clips(work)
    ok = [{"path": str(red), "start": 0.5, "end": 1.5}]
    with pytest.raises(clip.VideoError, match="start < end"):
        clip.edit([{"path": str(red), "start": 1.5, "end": 0.5}])
    with pytest.raises(clip.VideoError, match="transition"):
        clip.edit(ok, transition="fade")
    with pytest.raises(clip.VideoError, match="grade"):
        clip.edit(ok, grade="sepia")
    with pytest.raises(clip.VideoError, match="aspect"):
        clip.edit(ok, aspect="4:3")
    with pytest.raises(clip.VideoError, match="too short"):
        clip.edit(ok + [{"path": str(red), "start": 0.0, "end": 0.3}], transition="flash")
    with pytest.raises(clip.VideoError, match="at least one"):
        clip.edit([])
    first = clip.edit(ok, transition="cut", grade="none", name="same").path
    second = clip.edit(ok, transition="cut", grade="none", name="same").path
    assert first != second and first.exists() and second.exists()


def make_lavfi(path, graph, seconds):
    """A generated clip from an ffmpeg source (testsrc2 moves, gradients drifts, a colour is still)."""
    subprocess.run(
        [require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", graph,
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)],
        check=True,
    )  # fmt: skip
    return path


def test_highlights_pick_the_moving_stretches_not_the_still_ones(work):
    still = make_clip(work / "still.mp4", colours=((60, 60, 60),), each=6.0)
    dance = make_lavfi(work / "dance.mp4", "testsrc2=size=320x180:rate=25", 6.0)
    res = clip.highlights([still, dance], target_s=(3, 9), seg_s=3.0, name="pick")
    assert res.cuts and all(c["path"].endswith("dance.mp4") for c in res.cuts)
    assert 3 <= res.total <= 9 and all(c["end"] - c["start"] == pytest.approx(3.0, abs=0.05) for c in res.cuts)
    assert res.path.exists() and res.path.name == "pick.jpg" and res.json_path.exists()


def test_highlights_keep_different_moves_and_drop_repeats(work):
    dance = make_lavfi(work / "dance.mp4", "testsrc2=size=320x180:rate=25", 6.0)
    drift = make_lavfi(work / "drift.mp4", "gradients=s=320x180:rate=25:speed=0.05", 6.0)
    twin = work / "twin.mp4"
    twin.write_bytes(dance.read_bytes())  # the same take twice
    res = clip.highlights([dance, drift, twin], target_s=(9, 18), seg_s=3.0, name="styles")
    srcs = {Path(c["path"]).name for c in res.cuts}
    assert {"dance.mp4", "drift.mp4"} <= srcs  # both styles are in the reel
    assert "twin.mp4" not in srcs  # an identical take adds nothing new


def test_highlights_say_so_when_there_is_not_enough_to_reach_the_target(work):
    dance = make_lavfi(work / "dance.mp4", "testsrc2=size=320x180:rate=25", 6.0)
    res = clip.highlights([dance], target_s=(30, 60), seg_s=3.0, name="short")
    assert res.short and res.total < 30


def test_highlights_refuse_bad_targets_and_nothing_moving(work):
    still = make_clip(work / "still.mp4", colours=((60, 60, 60),), each=4.0)
    with pytest.raises(clip.VideoError, match="target_s"):
        clip.highlights([still], target_s=(60, 30))
    with pytest.raises(clip.VideoError, match="target_s"):
        clip.highlights([still], target_s=(30,))
    with pytest.raises(clip.VideoError, match="nothing moving"):
        clip.highlights([still], target_s=(3, 9), seg_s=3.0)


def test_highlights_feed_straight_into_edit(work):
    dance = make_lavfi(work / "dance.mp4", "testsrc2=size=320x180:rate=25", 6.0)
    res = clip.highlights([dance], target_s=(6, 9), seg_s=3.0, name="feed")
    out = clip.edit(res.cuts, transition="cut", grade="none", name="reel")
    assert clip.info(out.path).duration == pytest.approx(res.total, abs=0.15)


def test_warm_grade_warms_a_grey_clip_and_none_does_not(work):
    grey = make_clip(work / "grey.mp4", colours=((120, 120, 120),), each=1.5)
    cuts = [{"path": str(grey), "start": 0.0, "end": 1.0}]
    warm = clip.edit(cuts, transition="cut", grade="warm", name="warm")
    plain = clip.edit(cuts, transition="cut", grade="none", name="plain")
    w = _centre(clip.frame_at(warm.path, 0.5, 360))
    p = _centre(clip.frame_at(plain.path, 0.5, 360))
    assert w[0] - w[2] > 8 and abs(int(p[0]) - int(p[2])) < 6  # warm leans red-yellow, none stays neutral


def test_frame_crop_fills_the_frame_with_no_bars(work):
    red, _ = _edit_clips(work)
    cuts = [{"path": str(red), "start": 0.5, "end": 1.5}]
    res = clip.edit(cuts, transition="cut", grade="none", frame="crop", name="crop")
    assert _centre(clip.frame_at(res.path, 0.5, 360))[0] > 150
    assert clip.frame_at(res.path, 0.5, 360).getpixel((180, 5))[0] > 120  # red at the top: no black bar


def test_frame_blurred_bg_fills_the_bars_with_the_blurred_picture(work):
    red, _ = _edit_clips(work)
    cuts = [{"path": str(red), "start": 0.5, "end": 1.5}]
    res = clip.edit(cuts, transition="cut", grade="none", frame="blurred-bg", name="blur")
    top = clip.frame_at(res.path, 0.5, 360).getpixel((180, 5))[:3]
    assert top[0] > 90 and top[1] < 110  # the bar is the red picture, blurred, not black
    centre = _centre(clip.frame_at(res.path, 0.5, 360))
    assert centre[0] > 150  # the sharp picture is still in the middle


def test_unknown_frame_and_grade_names_are_refused(work):
    red, _ = _edit_clips(work)
    ok = [{"path": str(red), "start": 0.5, "end": 1.5}]
    with pytest.raises(clip.VideoError, match="frame must be one of"):
        clip.edit(ok, frame="stretch")
    with pytest.raises(clip.VideoError, match="grade must be one of"):
        clip.edit(ok, grade="sepia")


def test_preview_is_half_size_with_the_same_timing_and_is_smaller(work):
    red, blue = _edit_clips(work)
    cuts = [{"path": str(red), "start": 0.5, "end": 1.5}, {"path": str(blue), "start": 0.0, "end": 1.0}]
    final = clip.edit(cuts, transition="flash", grade="none", name="final")
    prev = clip.edit(cuts, transition="flash", grade="none", preview=True)
    pi = clip.info(prev.path)
    assert (pi.width, pi.height) == (360, 640) and pi.audio
    assert pi.duration == pytest.approx(clip.info(final.path).duration, abs=0.05)
    assert prev.preview and prev.path.name == "preview.mp4" and "preview" in prev.text()
    assert prev.path.stat().st_size < final.path.stat().st_size
    assert not final.preview and clip.info(final.path).height == 1280
