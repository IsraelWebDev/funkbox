"""Video tools: probe, scene cuts, trim, burned-in subtitles with exact text."""

import json
import subprocess

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
