"""Audio-loop mode: a section of whole bars cut from a track, crossfaded at the wrap, analysed as a circle, so a
loop reacts to the music and the loop check stays on."""

import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from funkbox.gl import Engine
from funkbox.gl.audio import analyze, read_wav_channels, wav_duration
from funkbox.gl.loopcheck import loop_check_entry, validate_loop
from funkbox.gl.recipe import Recipe2
from funkbox.loops import audioloop

ROOT = Path(__file__).resolve().parent.parent
SR = 22050
# reacts to the level, the bass and the spectrum, and moves by itself as well
REACT = """vec4 effect(vec2 p) {
  float bar = texture(iChannel3, vec2(abs(p.x) * 0.5, 0.5)).r;
  float ring = 0.5 + 0.5 * sin(8.0 * length(p) - iPhase + 3.0 * iAudio.x);
  return vec4(vec3(ring * (0.25 + 0.75 * iAudio.w)) + vec3(0.3 * bar, 0.0, 0.2 * iAudio.z), 1.0);
}
"""
LEVEL = "vec4 effect(vec2 p) { return vec4(vec3(iAudio.w), 1.0); }\n"


def write_wav(path: Path, x: np.ndarray, sr: int = SR, channels: int = 1) -> Path:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.stack([pcm, pcm // 2], axis=1).reshape(-1)  # the right channel at half level: order must survive
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def track(secs: float = 16.0, bpm: float = 120.0, sr: int = SR) -> np.ndarray:
    """A kick on every beat, a noise hit on beat 3 of each bar, and a tone that alternates bar by bar: the music
    repeats every 2 bars of 4 beats, so a 2- or 4-bar section loops and a 3-bar one does not."""
    n, period = int(secs * sr), 60.0 / bpm
    t = np.arange(n) / sr
    x = np.zeros(n)
    m = int(0.18 * sr)
    tt = np.arange(m) / sr
    kick = 0.7 * np.sin(2 * np.pi * 60 * tt) * np.exp(-tt * 25)
    hit = 0.25 * np.random.default_rng(7).standard_normal(m) * np.exp(-tt * 40)
    for k in range(int(secs / period)):
        s = int(round(k * period * sr))
        e = min(n, s + m)
        x[s:e] += kick[: e - s]
        if k % 4 == 2:
            x[s:e] += hit[: e - s]
    bar = (t // (4 * period)).astype(int)
    x += 0.12 * np.sin(2 * np.pi * np.where(bar % 2 == 0, 220.0, 277.0) * t)
    return x


def test_loop_analysis_is_periodic_and_plain_analysis_is_not(tmp_path):
    x = track(4.0) * np.linspace(0.2, 1.0, int(4.0 * SR))  # louder at the end than at the start
    wav = write_wav(tmp_path / "clip.wav", x)
    f = analyze(wav, fps=30, n_frames=120, loop=True)
    assert np.array_equal(f.bands[120], f.bands[0]) and np.array_equal(f.spectrum[120], f.spectrum[0])
    plain = analyze(wav, fps=30, n_frames=120)
    assert not np.allclose(plain.bands[120], plain.bands[0], atol=0.05)  # a clip played once ends where it ends
    # away from the wrap the two analyses see the same windows
    assert np.allclose(f.bands[10:110], plain.bands[10:110], atol=0.02)  # the 95th-percentile scale differs a little


def test_the_crossfade_makes_the_wrap_continue_the_track():
    x = np.arange(1000, dtype=np.float32)[:, None] / 1000.0  # a ramp: every sample is its own position
    out = audioloop.crossfade_cut(x, 100, 2.0, 30, 10, 0.5)  # 3 s from 2 s: samples 200..499, 50 crossfaded
    assert out.shape == (300, 1)
    assert out[0, 0] == pytest.approx(0.5) and out[-1, 0] == pytest.approx(0.499)  # the wrap goes 499 -> 500
    assert np.allclose(out[50:, 0], x[250:500, 0]) and np.all(np.diff(out[:50, 0]) < 0)  # fades back to 250
    tail = audioloop.crossfade_cut(x, 100, 7.0, 30, 10, 0.5)  # ends with the track: fade into what led up to it
    assert tail[-1, 0] == pytest.approx(0.699) and tail[0, 0] == pytest.approx(0.7)  # the wrap goes 699 -> 700
    assert np.allclose(tail[:250, 0], x[700:950, 0])
    with pytest.raises(ValueError, match="crossfade"):
        audioloop.crossfade_cut(x[:300], 100, 0.0, 30, 10, 0.5)  # the section is the whole track
    with pytest.raises(ValueError, match="past the end"):
        audioloop.crossfade_cut(x, 100, 8.0, 30, 10, 0.5)


def test_bar_counts_give_three_to_eight_seconds():
    assert audioloop.bar_counts(2.0) == [2, 3, 4]  # 4/4 at 120 BPM
    assert audioloop.bar_counts(60 / 180 * 3) == [3, 4, 5, 6, 7, 8]  # 3/4 at 180 BPM: a bar is 1 s
    assert audioloop.bar_counts(60 / 70 * 4) == [1, 2]
    assert audioloop.bar_counts(10.0) == []


def test_the_section_is_whole_bars_on_the_beat_and_its_ends_match(tmp_path):
    src = write_wav(tmp_path / "song.wav", track(), channels=2)
    sec = audioloop.cut_section(src, tmp_path / "o" / "song.loop.wav", fps=30)
    assert sec.picked == "auto" and sec.meter == 4 and abs(sec.bpm - 120.0) < 0.5
    assert sec.bars in (2, 4)  # the track repeats every 2 bars: 3 bars would not loop
    assert sec.frames == round(sec.seconds * 30) and 3.0 <= sec.seconds <= 8.0
    assert abs(sec.drift_ms) <= 1000 / 30 / 2 + 1  # rounded to whole frames: at most half a frame off the bars
    beat = sec.start_s / 0.5
    assert abs(beat - round(beat)) * 0.5 < 0.03  # starts on a beat
    out, sr = read_wav_channels(sec.path)
    assert sr == SR and out.shape == (round(sec.frames * SR / 30), 2)  # whole frames, both channels
    assert abs(wav_duration(sec.path) - sec.seconds) < 1e-3
    assert np.allclose(out[2000:, 1], out[2000:, 0] / 2, atol=2e-4)  # the channels were not mixed or swapped
    step = np.abs(np.diff(out[:, 0]))
    assert abs(out[0, 0] - out[-1, 0]) <= step.max() + 1e-4  # no click: the wrap is an ordinary sample step
    assert "bars of 4 beats" in sec.line() and "song.wav" in sec.line()


def test_a_given_start_snaps_to_the_nearest_beat_and_bars_are_taken_as_given(tmp_path):
    src = write_wav(tmp_path / "song.wav", track())
    sec = audioloop.cut_section(src, tmp_path / "a.wav", fps=30, start=5.1, bars=3)
    assert sec.picked == "start" and sec.bars == 3 and abs(sec.start_s - 5.0) < 0.03
    assert sec.seconds == pytest.approx(6.0, abs=0.05)
    one = audioloop.cut_section(src, tmp_path / "b.wav", fps=24, bars=1, meter=3)  # outside 3-8 s: the caller's call
    assert one.bars == 1 and one.meter == 3 and one.seconds == pytest.approx(1.5, abs=0.05) and one.fps == 24


def test_the_automatic_choice_skips_a_quiet_intro():
    y = track(20.0).astype(np.float32)
    y[: 8 * SR] *= 0.02  # 8 s of the same music, far quieter
    beats = [k * 0.5 for k in range(40)]
    start, bars, frames = audioloop.pick_section(y, SR, beats, 120.0, 30)
    assert start >= 7.5 and bars in (2, 4) and frames == bars * 60
    start, _, _ = audioloop.pick_section(y, SR, beats, 120.0, 30, start=1.0)  # asked for: the intro it is
    assert start == 1.0


def test_errors_say_what_to_change(tmp_path):
    src = write_wav(tmp_path / "song.wav", track())
    with pytest.raises(ValueError, match="give the bar count or a smaller meter"):
        audioloop.cut_section(src, tmp_path / "x.wav", fps=30, meter=20)  # a bar of 20 beats is 10 s
    with pytest.raises(ValueError, match="no section fits"):
        audioloop.cut_section(src, tmp_path / "x.wav", fps=30, bars=40)  # 80 s from a 16 s track
    with pytest.raises(ValueError, match="needs a track with a beat"):
        audioloop.cut_section(write_wav(tmp_path / "hum.wav", np.zeros(6 * SR)), tmp_path / "x.wav", fps=30)
    with pytest.raises(ValueError, match="1 or more"):
        audioloop.cut_section(src, tmp_path / "x.wav", fps=30, bars=0)
    assert not (tmp_path / "x.wav").exists()


def loop_recipe(audio: str, seconds: float, source: str = REACT, **settings) -> dict:
    st = {"width": 48, "height": 48, "fps": 30, "seconds": seconds, "audio": audio, "audio_loop": True, **settings}
    return {"settings": st, "layers": [{"type": "shader", "source": source}]}


def test_recipe_settings_for_an_audio_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    write_wav(tmp_path / "four.wav", track(4.0))
    rec = Recipe2.from_dict(loop_recipe("four.wav", 4.0))
    assert rec.settings.audio_loop is True and rec.to_dict()["settings"]["audio_loop"] is True
    plain = Recipe2.from_dict({"settings": {"seconds": 1.0}, "layers": [{"type": "shader", "source": LEVEL}]})
    assert "audio_loop" not in plain.to_dict()["settings"]  # recipes without it are written as before
    with pytest.raises(ValueError, match="needs settings.audio"):
        Recipe2.from_dict({"settings": {"audio_loop": True}, "layers": [{"type": "shader", "source": LEVEL}]})
    with pytest.raises(ValueError, match="true or false"):
        Recipe2.from_dict(loop_recipe("four.wav", 4.0, audio_loop="yes"))
    with pytest.raises(ValueError, match="must be one loop long"):  # a whole track marked as a loop
        Recipe2.from_dict(loop_recipe("four.wav", 2.0))


def test_apply_points_the_recipe_at_the_cut_section(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    write_wav(tmp_path / "song.wav", track())
    rec = Recipe2.from_dict(
        {"settings": {"fps": 30, "audio": "song.wav"}, "layers": [{"type": "shader", "source": REACT}]}
    )
    assert rec.settings.seconds == pytest.approx(16.0, abs=0.01)  # a whole track: its own length
    sec = audioloop.apply(rec, tmp_path / "out", "clip")
    st = rec.settings
    assert Path(st.audio) == tmp_path / "out" / "clip.loop.wav" and Path(st.audio).is_file()
    assert st.audio_loop is True and st.seconds == sec.seconds and st.frames == sec.frames
    with pytest.raises(ValueError, match="already set"):
        audioloop.apply(rec, tmp_path / "out", "again")


def test_a_reactive_loop_on_a_cut_section_passes_the_loop_check(gl, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    write_wav(tmp_path / "song.wav", track())
    rec = Recipe2.from_dict(
        {
            "settings": {"width": 48, "height": 48, "fps": 30, "audio": "song.wav"},
            "layers": [{"type": "shader", "source": REACT}],
        }
    )
    audioloop.apply(rec, tmp_path / "out", "clip")
    e = Engine(48, 48, gl)
    try:
        rep = validate_loop(rec, size=48, engine=e)
        n = rec.settings.frames
        frames = [e.render(rec, i, grain=False).astype(np.int16) for i in range(n)]
    finally:
        e.release()
    assert rep.ok, rep.reason
    assert rep.max_diff == 0 and rep.bad_pixel_frac == 0.0  # frame N is frame 0, to the pixel
    assert loop_check_entry(rep)["status"] == "ok"
    steps = [float(np.abs(b - a).mean()) for a, b in zip(frames, frames[1:])]
    assert max(steps) > 4 * (sum(steps) / len(steps))  # it does react: the kicks make steps far above the mean


def test_a_clip_that_does_not_wrap_fails_the_loop_check(gl, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    t = np.arange(4 * SR) / SR
    write_wav(tmp_path / "swell.wav", 0.8 * np.sin(2 * np.pi * 200 * t) * (t / 4.0))  # silent start, loud end
    rec = Recipe2.from_dict(loop_recipe("swell.wav", 4.0, source=LEVEL))
    e = Engine(48, 48, gl)
    try:
        rep = validate_loop(rec, size=48, engine=e)
    finally:
        e.release()
    assert not rep.ok and "jumps at the loop point" in rep.reason and "around the wrap" in rep.reason
    assert rep.max_diff == 0  # the frames still match at t=0 and t=1; it is the step into them that jumps


def test_cli_renders_an_audio_loop_with_the_check_on(gl, tmp_path):
    write_wav(tmp_path / "song.wav", track(), channels=2)
    (tmp_path / "react.frag").write_text(REACT, encoding="utf-8")
    env = {**os.environ, "FUNK_OUT": str(tmp_path / "out"), "FUNK_INPUTS": str(tmp_path)}
    base = [sys.executable, "-m", "funkbox", "loop", "render", "--shader", str(tmp_path / "react.frag")]
    base += ["--size", "48", "--fps", "30", "--audio", str(tmp_path / "song.wav"), "--out-dir", str(tmp_path / "out")]
    r = subprocess.run([*base, "--audio-loop", "--name", "al"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "audio loop: " in r.stdout and "loop check is skipped" not in r.stderr
    doc = json.loads((tmp_path / "out" / "al.json").read_text(encoding="utf-8"))
    sec = doc["audio_section"]
    assert doc["loop_check"]["status"] == "ok" and doc["settings"]["audio_loop"] is True
    assert Path(doc["settings"]["audio"]) == tmp_path / "out" / "al.loop.wav" == Path(sec["path"])
    assert doc["settings"]["seconds"] == sec["seconds"] and sec["bars"] in (2, 4) and sec["picked"] == "auto"
    assert (tmp_path / "out" / "al.mp4").stat().st_size > 0
    # the sidecar renders again by itself, check still on (the audio is already a loop: nothing is cut twice)
    again = [sys.executable, "-m", "funkbox", "loop", "render", "--recipe", str(tmp_path / "out" / "al.json")]
    r2 = subprocess.run(
        [*again, "--out-dir", str(tmp_path / "out"), "--name", "al2"], capture_output=True, text=True, cwd=ROOT, env=env
    )
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert json.loads((tmp_path / "out" / "al2.json").read_text(encoding="utf-8"))["loop_check"]["status"] == "ok"
    # a whole track is still rendered unchecked, and the warning names the way out
    r3 = subprocess.run([*base, "--seconds", "1", "--name", "whole"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r3.returncode == 0 and "--audio-loop" in r3.stderr, r3.stdout + r3.stderr
    whole = json.loads((tmp_path / "out" / "whole.json").read_text(encoding="utf-8"))
    assert whole["loop_check"] == {"status": "skipped", "reason": "audio"} and "audio_section" not in whole
    r4 = subprocess.run([*base, "--audio-loop", "--seconds", "4"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r4.returncode != 0 and "--audio-bars" in r4.stderr


def test_mcp_loop_render_cuts_the_section_and_reports_it(gl, tmp_path):
    pytest.importorskip("mcp")
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    home, out = tmp_path / "home", tmp_path / "out"
    (home / "inputs").mkdir(parents=True)
    write_wav(home / "inputs" / "song.wav", track())
    env = {**os.environ, "FUNK_OUT": str(out), "FUNK_HOME": str(home), "FUNK_INPUTS": str(home / "inputs")}
    if sys.platform == "linux" and "DISPLAY" not in env:
        env.setdefault("FUNK_GL_BACKEND", "egl")
    recipe = {
        "settings": {"width": 48, "height": 48, "fps": 30, "audio": "song.wav"},
        "layers": [{"type": "shader", "source": REACT}],
    }

    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=["-m", "funkbox", "serve"], env=env, cwd=str(ROOT))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                good = await s.call_tool("loop_render", {"recipe": recipe, "name": "mcp-al", "audio_loop": True})
                whole = await s.call_tool("loop_render", {"recipe": recipe, "name": "mcp-whole"})
                return ["\n".join(c.text for c in x.content if c.type == "text") for x in (good, whole)]

    good, whole = asyncio.run(scenario())
    assert "audio loop: " in good and "loop check: OK" in good, good
    doc = json.loads((out / "mcp-al.json").read_text(encoding="utf-8"))
    assert doc["loop_check"]["status"] == "ok" and doc["audio_section"]["bars"] in (2, 4)
    assert (out / "mcp-al.loop.wav").is_file() and (out / "mcp-al.mp4").is_file()
    assert "loop check skipped" in whole and "audio_loop=true" in whole  # a whole track: unchecked, with the way out


# ---------------------------------------------------------------- iBeat: the section's beat grid in the shader
FLASH = "vec4 effect(vec2 p) { return vec4(vec3(exp(-8.0 * iBeat.x)), 1.0); }\n"


def test_beat_vec_counts_beats_and_bars_and_wraps():
    from funkbox.gl.engine import beat_vec

    assert beat_vec(0.3, 0) == (0.0, 0.0, 0.0, 0.0)  # no grid: zeros, as iAudio without audio
    assert beat_vec(0.0, 8) == (0.0, 0.0, 0.0, 8.0)
    assert beat_vec(1.0, 8) == beat_vec(0.0, 8)  # t = 1 is t = 0: the loop holds
    x, y, z, w = beat_vec(5 / 8 + 1 / 16, 8, 4)  # halfway through beat 5, the second bar's second beat
    assert (x, y, z, w) == pytest.approx((0.5, 0.375, 5.0, 8.0))
    assert beat_vec(3 / 7, 7, 7)[:3] == pytest.approx((0.0, 3 / 7, 3.0))  # on a beat even when the float is a hair low


def test_recipe_settings_beats_and_meter(tmp_path):
    plain = {"settings": {"seconds": 2.0}, "layers": [{"type": "shader", "source": FLASH}]}
    rec = Recipe2.from_dict(plain)
    assert rec.settings.beats == 0 and "beats" not in rec.to_dict()["settings"]  # written as before
    assert "meter" not in rec.to_dict()["settings"]
    rec = Recipe2.from_dict({**plain, "settings": {"seconds": 2.0, "beats": 6, "meter": 3}})
    assert rec.to_dict()["settings"]["beats"] == 6 and rec.to_dict()["settings"]["meter"] == 3
    with pytest.raises(ValueError, match="whole number of bars"):
        Recipe2.from_dict({**plain, "settings": {"seconds": 2.0, "beats": 6}})  # 6 beats are not whole bars of 4
    for bad in ({"beats": -1}, {"beats": 2.5}, {"beats": True}, {"meter": 0}):
        with pytest.raises(ValueError, match="whole number"):
            Recipe2.from_dict({**plain, "settings": {"seconds": 2.0, **bad}})


def test_apply_sets_the_sections_beat_grid(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    write_wav(tmp_path / "song.wav", track())
    rec = Recipe2.from_dict(
        {"settings": {"fps": 30, "audio": "song.wav"}, "layers": [{"type": "shader", "source": FLASH}]}
    )
    sec = audioloop.apply(rec, tmp_path / "out", "clip")
    assert rec.settings.beats == sec.bars * sec.meter and rec.settings.meter == sec.meter
    again = Recipe2.from_dict(rec.to_dict())  # the sidecar renders again with the same grid
    assert (again.settings.beats, again.settings.meter) == (rec.settings.beats, rec.settings.meter)


def test_a_flash_on_every_beat_lands_on_the_beat_and_still_loops(gl, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    write_wav(tmp_path / "song.wav", track())
    rec = Recipe2.from_dict(
        {
            "settings": {"width": 32, "height": 32, "fps": 30, "audio": "song.wav"},
            "layers": [{"type": "shader", "source": FLASH}],
        }
    )
    audioloop.apply(rec, tmp_path / "out", "clip")
    st = rec.settings
    e = Engine(32, 32, gl)
    try:
        rep = validate_loop(rec, size=32, engine=e)
        level = [float(e.render(rec, i, grain=False).mean()) for i in range(st.frames)]
    finally:
        e.release()
    assert rep.ok, rep.reason
    on_beat = {round(k * st.frames / st.beats) for k in range(st.beats)}
    peaks = {i for i in range(st.frames) if level[i] > level[i - 1] and level[i] >= level[(i + 1) % st.frames]}
    assert peaks == on_beat  # one flash per beat, on the frame nearest the beat
