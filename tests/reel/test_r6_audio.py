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
    for k in range(1, 12):  # the beat grid lands on the clicks (<= 40 ms)
        b = min(info.beats, key=lambda x: abs(x - k * period))
        assert abs(b - k * period) < 0.04


def test_click_track_with_bass_pad_is_still_120(work):
    p = work / "padded.wav"
    write_click(p, 120, 20.0, pad=True)
    assert abs(audio.detect_beats(p).bpm - 120) <= 1.0


SR = 22050
P128 = 60.0 / 128


def synth_kick_hat(first=0.0, seconds=12.0, bpm=128.0, hats=True):
    """A kick on every beat from ``first`` and (``hats``) a loud hi-hat on every off-beat."""
    period = 60.0 / bpm
    y = np.zeros(int(SR * seconds))
    rng = np.random.default_rng(1)

    def add(t0, sig, amp):
        i0 = int(t0 * SR)
        n = min(len(sig), len(y) - i0)
        if n > 0:
            y[i0 : i0 + n] += amp * sig[:n]

    k = 0
    while first + k * period < seconds - 0.2:
        t = np.arange(int(0.25 * SR)) / SR
        add(first + k * period, np.sin(2 * np.pi * (50 + 70 * np.exp(-t * 30)) * t) * np.exp(-t * 18), 1.0)
        if hats:
            h = rng.standard_normal(int(0.04 * SR)) * np.exp(-np.arange(int(0.04 * SR)) / SR * 90)
            add(first + k * period + period / 2, np.diff(np.concatenate([[0.0], h])) * 4.0, 0.5)
        k += 1
    return (y / np.abs(y).max() * 0.9).astype(np.float32)


def test_beat_phase_comes_from_the_kick_not_the_off_beat_hats():
    info = audio.beat_grid(synth_kick_hat(), SR)
    assert abs(info.bpm - 128) < 0.5, info.bpm
    assert len(info.beats) >= 8
    for k in range(8):
        assert abs(info.beats[k] - k * P128) <= 0.02, (k, info.beats[:8])
    assert audio._beat_analysis(synth_kick_hat(), SR)[3] == audio.PHASE_LOW


def test_beat_phase_follows_a_kick_that_starts_late():
    y = synth_kick_hat(first=0.1)
    info = audio.beat_grid(y, SR)
    assert abs(info.bpm - 128) < 0.5, info.bpm
    assert audio._beat_analysis(y, SR)[3] == audio.PHASE_LOW
    assert abs(info.beats[0] - 0.1) <= 0.02, info.beats[:3]  # the first beat is the kick, not a beat added at 0.0
    for k in range(8):
        assert abs(info.beats[k] - (0.1 + k * P128)) <= 0.02, (k, info.beats[:9])


def _write_wav(path, y):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def _music_reel(path):
    from types import SimpleNamespace

    a = SimpleNamespace(bpm=None, beat_offset=0.0, music=str(path))
    return SimpleNamespace(audio=a, base_dir=path.parent)


@pytest.mark.parametrize("first", [0.0, 0.1, 0.45])
def test_reel_grid_for_a_detected_track_lands_on_the_kicks(work, first):
    """beat_grid_for is the grid reels cut against: every kick, early and late in the reel, within 20 ms."""
    p = work / f"kick{first}.wav"
    _write_wav(p, synth_kick_hat(first=first))
    grid = audio.beat_grid_for(_music_reel(p))
    for k in (0, 1, 2, 5, 15, 20):
        kick = first + k * P128
        assert min(abs(b - kick) for b in grid) <= 0.02, (first, k, grid[:6])
    # the clamp of a first beat just before 0 s moves only that beat: the period stays the detected period
    steps = [b - a for a, b in zip(grid[1:20], grid[2:21])]
    assert max(steps) - min(steps) < 0.001, steps


def test_a_beat_before_zero_is_kept_at_zero_only_for_a_low_band_phase():
    y = synth_kick_hat(first=0.0)
    bpm, phase, fps, source = audio._beat_analysis(y, SR)
    assert source == audio.PHASE_LOW
    info = audio.beat_grid(y, SR)
    assert info.beats[0] == 0.0 and abs(info.beats[1] - 60.0 / bpm) <= 0.02, info.beats[:3]
    # a kick at 0.45 s has no beat at 0.0: the clamp adds nothing where there is no kick
    assert audio.beat_grid(synth_kick_hat(first=0.45), SR).beats[0] > 0.3


def test_a_bass_drone_without_a_kick_falls_back_to_the_full_band_phase():
    """Low-band energy but no steady pulse in it (a held 55 Hz note under off-beat hi-hats): the pulse ratio decides."""
    rng = np.random.default_rng(1)
    y = np.zeros(int(SR * 12.0))
    k = 0
    while k * P128 < 11.8:
        h = np.diff(
            np.concatenate([[0.0], rng.standard_normal(int(0.04 * SR)) * np.exp(-np.arange(int(0.04 * SR)) / SR * 90)])
        )
        i0 = int((k * P128 + P128 / 2) * SR)
        n = min(len(h), len(y) - i0)
        if n > 0:
            y[i0 : i0 + n] += 2.0 * h[:n]
        k += 1
    y += 0.3 * np.abs(y).max() * np.sin(2 * np.pi * 55 * np.arange(len(y)) / SR)
    y = (y / np.abs(y).max() * 0.9).astype(np.float32)
    assert audio._band_share(y, SR, audio.LOW_BAND_HZ) >= audio.LOW_BAND_MIN_SHARE  # the energy test passes...
    assert audio._beat_analysis(y, SR)[3] == audio.PHASE_FULL  # ...the pulse test does not
    assert audio.beat_grid(y, SR).beats


def test_no_low_band_pulse_falls_back_to_the_full_band_phase():
    """High-frequency noise bursts only: nothing at or below 150 Hz, so the named fallback picks the phase."""
    rng = np.random.default_rng(3)
    y = np.zeros(int(SR * 12.0))
    k = 0
    while k * P128 < 11.8:
        i0 = int((0.1 + k * P128) * SR)
        n = int(0.03 * SR)
        burst = np.diff(np.concatenate([[0.0], rng.standard_normal(n)])) * np.exp(-np.arange(n) / SR * 150)
        y[i0 : i0 + n] += burst[: max(0, min(n, len(y) - i0))] if i0 < len(y) else 0
        k += 1
    y = (y / np.abs(y).max() * 0.9).astype(np.float32)
    bpm, phase, fps, source = audio._beat_analysis(y, SR)
    assert source == audio.PHASE_FULL
    info = audio.beat_grid(y, SR)
    assert abs(info.bpm - 128) < 1.0 and info.beats
    # the fallback is the full-band phase: the same grid the whole-spectrum envelope gives
    env, efps = audio.onset_envelope(y, SR)
    assert phase == audio._comb_score(env, np.arange(len(env)), efps, bpm)[1]


def test_click120_first_beat_is_at_zero_and_the_second_one_period_later(work):
    p = work / "click120.wav"
    write_click(p, 120, 30.0, pad=True)  # the examples/reel_demo fixture: 30 s of clicks over a quiet bass pad
    info = audio.detect_beats(p)
    assert info.phase_source == audio.PHASE_LOW  # a silent low-band fallback would pick another phase
    assert info.beats[0] == 0.0, info.beats[:3]
    assert abs(info.beats[1] - 60.0 / 120) <= 0.01, info.beats[:3]


def _upsample(y, factor=2):
    return np.interp(np.arange(len(y) * factor) / factor, np.arange(len(y)), y).astype(np.float32)


@pytest.mark.parametrize("first", [0.0, 0.1])
def test_the_grid_at_44100_matches_the_grid_at_22050_for_the_synthetic_probe(first):
    y = synth_kick_hat(first=first)
    lo = audio.beat_grid(y, SR)
    hi = audio.beat_grid(_upsample(y), 2 * SR)
    assert hi.phase_source == audio.PHASE_LOW  # a silent full-band fallback would fail the 5 ms match
    # an old fixed 10 ms lead fails here at first=0.0: 6.9 ms across the first 8 beats, over the 5 ms limit
    assert abs(lo.bpm - hi.bpm) < 0.5, (lo.bpm, hi.bpm)
    assert len(lo.beats) >= 8 and len(hi.beats) >= 8
    for k in range(8):
        assert abs(lo.beats[k] - hi.beats[k]) <= 0.005, (k, lo.beats[:8], hi.beats[:8])


def test_the_old_fixed_lead_drifts_the_first_beat_away_from_zero(monkeypatch):
    """The scaled lead is not cosmetic: at 44100 Hz the real lead is 5 ms, so pinning it to the old fixed
    10 ms pushes the first beat of the 128 BPM probe (a kick at 0.0) more than 5 ms away from 0.0 - the
    comparison the grid-match test above asserts at that tolerance."""
    y = _upsample(synth_kick_hat(first=0.0))
    ok = audio.beat_grid(y, 2 * SR)  # the positive control: with the real scaled lead the kick lands on 0.0
    assert abs(ok.beats[0]) <= 0.005, ok.beats[:3]
    monkeypatch.setattr(audio, "_low_band_lead_s", lambda sr, n_fft=1024: 0.010)  # the old fixed lead
    assert audio._beat_analysis(y, 2 * SR)[3] == audio.PHASE_LOW
    info = audio.beat_grid(y, 2 * SR)
    assert abs(info.beats[0]) > 0.005, info.beats[:3]


def test_the_grid_at_44100_matches_the_grid_at_22050_for_the_click_track(work):
    p = work / "click120.wav"
    write_click(p, 120, 30.0, pad=True)
    lo = audio.beat_grid(audio.decode_mono(p, 22050), 22050)
    hi = audio.beat_grid(audio.decode_mono(p, 44100), 44100)
    assert lo.phase_source == audio.PHASE_LOW and hi.phase_source == audio.PHASE_LOW
    for k in range(8):
        assert abs(lo.beats[k] - hi.beats[k]) <= 0.005, (k, lo.beats[:8], hi.beats[:8])


def test_the_low_band_lead_scales_with_the_window_and_the_sample_rate():
    assert audio._low_band_lead_s(22050) == pytest.approx(audio.LOW_BAND_LEAD_S)
    assert audio._low_band_lead_s(44100) == pytest.approx(audio.LOW_BAND_LEAD_S / 2)
    assert audio._low_band_lead_s(44100, n_fft=2048) == pytest.approx(audio.LOW_BAND_LEAD_S)


def test_a_very_low_low_hz_takes_the_full_band_fallback_and_its_phase():
    y = synth_kick_hat()
    bpm, phase, fps, source = audio._beat_analysis(y, SR, low_hz=20)
    assert source == audio.PHASE_FULL
    env, efps = audio.onset_envelope(y, SR)
    assert phase == audio._comb_score(env, np.arange(len(env)), efps, bpm)[1]


def _grid_of(monkeypatch, work, info):
    p = work / "m.wav"
    _write_wav(p, np.zeros(SR, np.float32))
    monkeypatch.setattr(audio, "_BEAT_CACHE", {})
    monkeypatch.setattr(audio, "detect_beats", lambda path: info)
    return audio.beat_grid_for(_music_reel(p))


def test_a_full_band_grid_whose_first_beat_is_zero_is_returned_unchanged(monkeypatch, work):
    beats = [0.0, 0.7, 1.4, 2.1]  # bpm 100 -> period 0.6: a rebuild from beats[1] would start at 0.1
    grid = _grid_of(monkeypatch, work, audio.BeatInfo(100.0, beats, 3.0, audio.PHASE_FULL))
    assert grid[:3] == [0.0, 0.6, 1.2], grid[:4]


def test_a_low_band_grid_whose_first_beat_is_zero_is_rebuilt_from_the_second_beat(monkeypatch, work):
    beats = [0.0, 0.7, 1.4, 2.1]
    grid = _grid_of(monkeypatch, work, audio.BeatInfo(100.0, beats, 3.0, audio.PHASE_LOW))
    assert grid[:3] == [0.1, 0.7, 1.3], grid[:4]


def test_stub_tts_length_is_proportional_to_characters(work):
    assert audio.tts_mode() == "stub"
    deck = parse_copy(
        {
            "lines": {
                "a": {"text": "ABCDEFGHIJ"},
                "b": {"text": "ABCDEFGHIJ" * 4},
                "c": {"text": "GLOWMOP", "say": "glow mop is here today"},
            }
        }
    )
    da, db, dc = (audio.wav_duration(audio.synthesize(deck, k)) for k in "abc")
    assert db == pytest.approx(4 * da, rel=0.1)
    assert dc == pytest.approx(len("glow mop is here today") * audio.STUB_SEC_PER_CHAR, abs=0.05)  # `say` is spoken


def test_tts_cache_key_depends_on_spoken_text(work):
    d1 = parse_copy({"lines": {"a": {"text": "GLOWMOP"}}, "pronounce": {"GLOWMOP": "glow mop"}})
    d2 = parse_copy({"lines": {"a": {"text": "GLOWMOP"}}})
    assert audio.synthesize(d1, "a") != audio.synthesize(d2, "a")


@pytest.mark.skipif(
    not (__import__("importlib").util.find_spec("piper") and audio.voice_files()[0].exists()),
    reason="Piper or its voice model not installed",
)
def test_real_piper_voice_speaks(monkeypatch):
    monkeypatch.delenv("REEL_TTS", raising=False)
    assert audio.tts_mode("piper") == "piper"
    deck = parse_copy({"lines": {"a": {"text": "Look closer. Time for a clean?"}}})
    p = audio.synthesize(deck, "a", "piper")
    d = audio.wav_duration(p)
    assert 1.0 < d < 6.0
    with wave.open(str(p)) as wf:
        y = np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(float) / 32768
    assert np.sqrt((y**2).mean()) > 0.01


# ---------------------------------------------------------------- the mixed demo
def rms_db(mp4, start, dur):
    r = subprocess.run(
        [
            FF,
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{dur:.3f}",
            "-i",
            str(mp4),
            "-vn",
            "-af",
            "astats=metadata=1:reset=0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    vals = re.findall(r"RMS level dB:\s*(-?[\d.]+|-inf)", r.stderr)
    assert vals, r.stderr[-400:]
    v = vals[-1]  # last = Overall
    return -999.0 if v == "-inf" else float(v)


def integrated_lufs(mp4):
    r = subprocess.run(
        [FF, "-hide_banner", "-nostats", "-i", str(mp4), "-vn", "-af", "ebur128", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    summ = r.stderr[r.stderr.rindex("Summary:") :]
    return float(re.search(r"I:\s*(-?[\d.]+) LUFS", summ).group(1))


def _piper_ready():
    import importlib.util

    return bool(importlib.util.find_spec("piper")) and audio.voice_files()[0].exists()


def _kokoro_ready():
    return audio._kokoro_ready()


@pytest.mark.skipif(not audio._kokoro_ready(), reason="kokoro-onnx or the Kokoro model not installed")
@pytest.mark.parametrize("voice", audio.APPROVED_VOICES)
def test_real_kokoro_voices_speak(voice, monkeypatch):
    monkeypatch.delenv("REEL_TTS", raising=False)
    assert audio.tts_mode(voice) == "kokoro"
    deck = parse_copy({"lines": {"a": {"text": "Look closer. Time for a clean?"}}})
    p = audio.synthesize(deck, "a", voice)
    fast = audio.synthesize(deck, "a", voice, 1.3)
    assert p != fast and 1.0 < audio.wav_duration(p) < 6.0 and audio.wav_duration(fast) < audio.wav_duration(p)
    with wave.open(str(p)) as wf:
        assert wf.getframerate() == 24000
        y = np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(float) / 32768
    assert np.sqrt((y**2).mean()) > 0.01


@pytest.fixture(scope="module", params=["stub", "piper"])
def mixed(request, demo, gl):
    """The demo reel mixed with the stub voice and (when installed) with the real Piper voice."""
    import os

    from funkbox.config import funk_out

    if request.param == "piper" and not _piper_ready():
        pytest.skip("Piper voice not installed")
    old = os.environ.get("REEL_TTS")
    old_voice = os.environ.get("FUNK_VOICE")
    if request.param == "piper":
        os.environ.pop("REEL_TTS", None)
        os.environ["FUNK_VOICE"] = "piper"
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
        if old_voice is None:
            os.environ.pop("FUNK_VOICE", None)
        else:
            os.environ["FUNK_VOICE"] = old_voice


def test_vo_is_6db_louder_than_music_only_windows(mixed):
    plan, out, mp4 = mixed
    vo = [(sp.vo_start + 0.15, sp.vo_dur - 0.3) for sp in plan.spans if sp.vo_id]
    music = [
        (sp.start + 0.1, sp.duration - 0.2)
        for sp in plan.spans
        if sp.kind == "shot" and not sp.vo_id and sp.duration >= 1.0
    ]
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
    assert "volume='1-0.68377*" in g.replace(" ", "")  # -10 dB = 0.316 -> 1-0.684 depth
    assert g.count("adelay=") == len(clips)


def test_end_card_is_silent(mixed):
    plan, out, mp4 = mixed
    end = plan.spans[-1]
    assert end.kind == "end"
    assert rms_db(mp4, end.start + 0.05, end.duration - 0.1) < -50.0
    assert rms_db(mp4, 0.2, 1.0) > -40.0  # audio is there before the card


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
    r = subprocess.run(
        [
            FF,
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{end.start + 0.5:.2f}",
            "-t",
            "2",
            "-i",
            str(wav),
            "-af",
            "astats=metadata=1:reset=0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
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
