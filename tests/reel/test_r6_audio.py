import json
import re
import subprocess
import wave

import numpy as np
import pytest

from funkbox.media import ffmpeg_exe
from funkbox.reel import audio, render
from funkbox.reel.copydeck import parse_copy

FF = ffmpeg_exe()


def write_click(path, bpm, seconds=12.0, sr=44100, pad=False):
    import make_assets
    make_assets.click_track(path, bpm=bpm, seconds=seconds, sr=sr, pad=pad)


@pytest.mark.parametrize("bpm", [120, 100, 140])
def test_click_track_tempo_within_one_bpm(work, bpm):
    p = work / f"click{bpm}.wav"
    write_click(p, bpm)
    info = audio.detect_beats(p)
    assert abs(info.bpm - bpm) <= 1.0, info.bpm
    period = 60.0 / bpm
    for k in range(1, 12):                                    # the beat grid lands on the clicks (<= 40 ms)
        b = min(info.beats, key=lambda x: abs(x - k * period))
        assert abs(b - k * period) < 0.04


def test_click_track_with_bass_pad_is_still_120(work):
    p = work / "padded.wav"
    write_click(p, 120, 20.0, pad=True)
    assert abs(audio.detect_beats(p).bpm - 120) <= 1.0


def test_stub_tts_length_is_proportional_to_characters(work):
    assert audio.tts_mode() == "stub"
    deck = parse_copy({"lines": {"a": {"text": "ABCDEFGHIJ"}, "b": {"text": "ABCDEFGHIJ" * 4},
                                 "c": {"text": "GLOWMOP", "say": "glow mop is here today"}}})
    da, db, dc = (audio.wav_duration(audio.synthesize(deck, k)) for k in "abc")
    assert db == pytest.approx(4 * da, rel=0.1)
    assert dc == pytest.approx(len("glow mop is here today") * audio.STUB_SEC_PER_CHAR, abs=0.05)   # `say` is spoken


def test_tts_cache_key_depends_on_spoken_text(work):
    d1 = parse_copy({"lines": {"a": {"text": "GLOWMOP"}}, "pronounce": {"GLOWMOP": "glow mop"}})
    d2 = parse_copy({"lines": {"a": {"text": "GLOWMOP"}}})
    assert audio.synthesize(d1, "a") != audio.synthesize(d2, "a")


@pytest.mark.skipif(not (__import__("importlib").util.find_spec("piper") and audio.voice_files()[0].exists()),
                    reason="Piper or its voice model not installed")
def test_real_piper_voice_speaks(monkeypatch):
    monkeypatch.delenv("REEL_TTS", raising=False)
    assert audio.tts_mode() == "piper"
    deck = parse_copy({"lines": {"a": {"text": "Look closer. Time for a clean?"}}})
    p = audio.synthesize(deck, "a")
    d = audio.wav_duration(p)
    assert 1.0 < d < 6.0
    with wave.open(str(p)) as wf:
        y = np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(float) / 32768
    assert np.sqrt((y ** 2).mean()) > 0.01


# ---------------------------------------------------------------- the mixed demo
def rms_db(mp4, start, dur):
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(mp4),
                        "-vn", "-af", "astats=metadata=1:reset=0", "-f", "null", "-"], capture_output=True, text=True)
    vals = re.findall(r"RMS level dB:\s*(-?[\d.]+|-inf)", r.stderr)
    assert vals, r.stderr[-400:]
    v = vals[-1]                                                  # last = Overall
    return -999.0 if v == "-inf" else float(v)


def integrated_lufs(mp4):
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", str(mp4), "-vn", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    summ = r.stderr[r.stderr.rindex("Summary:"):]
    return float(re.search(r"I:\s*(-?[\d.]+) LUFS", summ).group(1))


def _piper_ready():
    import importlib.util
    return bool(importlib.util.find_spec("piper")) and audio.voice_files()[0].exists()


@pytest.fixture(scope="module", params=["stub", "piper"])
def mixed(request, demo, gl):
    """The demo reel mixed with the stub voice and (when installed) with the real Piper voice."""
    import os

    from funkbox.config import funk_out
    if request.param == "piper" and not _piper_ready():
        pytest.skip("Piper voice not installed")
    old = os.environ.get("REEL_TTS")
    if request.param == "piper":
        os.environ.pop("REEL_TTS", None)
    else:
        os.environ["REEL_TTS"] = "stub"
    try:
        out_dir = funk_out() / "test_work" / f"r6_mixed_{request.param}"
        out_dir.mkdir(parents=True, exist_ok=True)
        d = json.loads((demo / "demo.reel.json").read_text())
        d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
        plan = render.prepare(d, demo)
        assert audio.tts_mode() == request.param
        out = render.render_reel(d, "mixed", base_dir=demo, out_dir=out_dir, with_audio=True, plan=plan)
        yield plan, out, out_dir / "mixed.mp4"
    finally:
        if old is None:
            os.environ.pop("REEL_TTS", None)
        else:
            os.environ["REEL_TTS"] = old


def test_vo_is_6db_louder_than_music_only_windows(mixed):
    plan, out, mp4 = mixed
    vo = [(sp.vo_start + 0.15, sp.vo_dur - 0.3) for sp in plan.spans if sp.vo_id]
    music = [(sp.start + 0.1, sp.duration - 0.2) for sp in plan.spans if sp.kind == "shot" and not sp.vo_id
             and sp.duration >= 1.0]
    assert len(vo) >= 2 and len(music) >= 4
    vo_levels = [rms_db(mp4, s, d) for s, d in vo]
    music_levels = [rms_db(mp4, s, d) for s, d in music]
    assert min(vo_levels) >= max(music_levels) + 6.0, (vo_levels, music_levels)


def test_music_is_ducked_while_vo_plays(demo, work):
    """duck_db really lowers the music under VO: same reel, VO muted by a tiny gain isn't possible, so check
    the filtergraph carries the automation and its depth."""
    d = json.loads((demo / "demo.reel.json").read_text())
    plan = render.prepare(d, demo)
    clips = [(audio.synthesize(plan.deck, sp.vo_id), sp.vo_start) for sp in plan.spans if sp.vo_id]
    args, g = audio.mix_graph(plan, demo / "inputs/music/click120.wav", clips, plan.spans[-1].end, None)
    assert "volume='1-0.68377*" in g.replace(" ", "")                      # -10 dB = 0.316 -> 1-0.684 depth
    assert g.count("adelay=") == len(clips)


def test_end_card_is_silent(mixed):
    plan, out, mp4 = mixed
    end = plan.spans[-1]
    assert end.kind == "end"
    assert rms_db(mp4, end.start + 0.05, end.duration - 0.1) < -50.0
    assert rms_db(mp4, 0.2, 1.0) > -40.0                                   # audio is there before the card


def test_integrated_loudness_hits_target(mixed):
    plan, out, mp4 = mixed
    assert abs(integrated_lufs(mp4) - plan.reel.audio.loudness_lufs) <= 1.0


def test_mux_is_aac_48k_stereo_and_video_untouched(mixed):
    plan, out, mp4 = mixed
    pr = subprocess.run([FF, "-hide_banner", "-i", str(mp4)], capture_output=True, text=True).stderr
    assert "Audio: aac" in pr and "48000 Hz, stereo" in pr and "Video: h264" in pr
    dur = float(re.search(r"Duration: (\d+):(\d+):([\d.]+)", pr).group(3))
    assert dur == pytest.approx(plan.spans[-1].end, abs=0.35)


def test_end_card_audio_keep_is_not_muted(demo, gl, work):
    d = json.loads((demo / "demo.reel.json").read_text())
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    d["end_card"]["audio"] = "keep"
    plan = render.prepare(d, demo)
    mix = audio.build_mix(plan, work, "keep")
    end = plan.spans[-1]
    wav = work / "keep.mix.wav"
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-ss", f"{end.start + 0.5:.2f}", "-t", "2", "-i", str(wav),
                        "-af", "astats=metadata=1:reset=0", "-f", "null", "-"], capture_output=True, text=True)
    lvl = float(re.findall(r"RMS level dB:\s*(-?[\d.]+)", r.stderr)[-1])
    assert lvl > -50


def test_reel_without_music_or_vo_has_no_audio_track(demo, gl, work):
    d = json.loads((demo / "demo.reel.json").read_text())
    d["settings"] = {"width": 270, "height": 480, "fps": 10}
    d["audio"] = {"voiceover": False}
    for sh in d["shots"]:
        sh["duration"] = 1.0
        sh.pop("vo", None)
    out = render.render_reel(d, "mute", base_dir=demo, out_dir=work)
    pr = subprocess.run([FF, "-hide_banner", "-i", out.paths["mp4"]], capture_output=True, text=True).stderr
    assert "Audio:" not in pr and "Video: h264" in pr


def test_vo_only_reel_mixes(demo, gl, work):
    d = json.loads((demo / "demo.reel.json").read_text())
    d["settings"] = {"width": 270, "height": 480, "fps": 10}
    d["audio"] = {"voiceover": True, "loudness_lufs": -16}
    for sh in d["shots"]:
        sh["duration"] = "auto" if sh.get("vo") else 1.0
    plan = render.prepare(d, demo)
    mix = audio.build_mix(plan, work, "vo_only")
    assert mix and mix.exists()
