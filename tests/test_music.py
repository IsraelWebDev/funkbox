"""Music: the SoundFont player, the score language and rendering, and reels using a score."""
import json
import math
import wave

import numpy as np
import pytest

from funkbox.music import score, sf2


def peak_hz(x, sr=48000):
    x = x[:, 0] if x.ndim == 2 else x
    x = x[: 1 << int(math.log2(len(x)))]
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(spec) * sr / len(x)


@pytest.fixture()
def bank(tmp_path):
    rate = 44100
    n = int(round(100 * rate / 440.0))
    t = np.arange(n * 3) / rate
    sine = np.sin(2 * np.pi * (100 * rate / n) * t) * 0.8              # exactly 100 periods per loop
    hit = np.random.default_rng(1).uniform(-1, 1, 4410) * np.exp(-np.arange(4410) / 800)
    p = tmp_path / "t.sf2"
    sf2.write_sf2(p, [
        {"name": "Sine", "program": 0, "wave": sine, "root": 69, "loop": (n, 2 * n),
         "gens": {sf2.G_RELEASE: int(1200 * math.log2(0.2))}},
        {"name": "OneShot", "program": 5, "wave": sine[: rate // 10], "root": 69},
        {"name": "Kit", "program": 0, "bank": 128, "zones": [{"wave": hit, "root": 36, "keys": (36, 36)}]}], rate)
    return sf2.SoundFont(p)


def test_presets_and_pitch(bank):
    assert bank.presets() == [(0, 0, "Sine"), (0, 5, "OneShot"), (128, 0, "Kit")]
    a = bank.note(0, 0, 69, 127, 1.0)
    assert peak_hz(a) == pytest.approx(440, abs=3)
    assert peak_hz(bank.note(0, 0, 81, 127, 1.0)) == pytest.approx(880, abs=4)     # an octave up from the root
    assert peak_hz(bank.note(0, 0, 62, 127, 1.0)) == pytest.approx(293.66, abs=3)


def test_loops_sustain_and_release_fades(bank):
    a = bank.note(0, 0, 69, 127, 1.0)
    held, tail = a[int(0.9 * 48000):int(0.95 * 48000)], a[-2000:]
    assert np.abs(held).max() > 0.5 and np.abs(tail).max() < 0.01
    assert len(a) == pytest.approx(48000 * 1.2, abs=2)                             # held + release
    one = bank.note(0, 5, 69, 127, 1.0)                                             # no loop: stops with the sample
    assert len(one) <= 48000 * 0.11
    soft = bank.note(0, 0, 69, 40, 0.5)
    assert np.abs(soft).max() < np.abs(a).max() * 0.2                               # velocity
    assert np.abs(bank.note(128, 0, 36, 100, 0.1)).max() > 0.1 and np.abs(bank.note(128, 0, 40, 100, 0.1)).max() == 0


def test_the_score_language():
    d = score.key_root("D")
    fr = score.SCALES["freygish"]
    ev, length = score.parse_notes("^1/8 ^2 ^3 | [^1 ^5]/2. r/4 ~ ^1' ^5,! E4?", d, fr, 4, 90, 0)
    assert [e.keys for e in ev] == [[62], [63], [66], [62, 69], [74], [57], [64]]
    assert ev[2].start == 1.0 and ev[3].start == 1.5
    assert ev[3].beats == 4.0                                  # dotted half, then held one more beat by ~ (after r)
    assert ev[4].start == 6.5 and length == 9.5
    assert ev[5].velocity == 110 and ev[6].velocity == 60
    ev, _ = score.parse_notes("D4/4 ~/4", d, fr, 4, 90, 0)
    assert ev[0].beats == 2.0
    for bad, msg in (("H4", "cannot read"), ("D4/3", "not a note length"), ("~", "needs a note"), ("[]", "empty")):
        with pytest.raises(score.ScoreError, match=msg):
            score.parse_notes(bad, d, fr, 4, 90, 0)
    errs = score.check({"bpm": 400, "scale": "nope", "tracks": [{"program": 300}, {"drums": True}]})
    assert {"bpm: 30..240", "tracks[0]: needs `notes` (or \"drums\": true and a `pattern`)"} <= set(errs)
    assert any("scale" in e for e in errs) and any("pattern" in e for e in errs) and any("program" in e for e in errs)


def test_render_with_a_given_soundfont_fills_the_bars(bank, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    sc = {"bpm": 120, "bars": 2, "key": "A", "scale": "minor", "soundfont": str(bank.path), "reverb": 0.1,
          "tracks": [{"name": "lead", "program": 0, "octave": 4, "notes": "^1/2"},
                     {"name": "drums", "drums": True, "pattern": {"kick": "x...x...x...x..."}}]}
    m = score.render(sc, tmp_path / "o.wav")
    assert 4.0 <= m.seconds <= 6.0 and m.beats[:3] == [0.0, 0.5, 1.0] and len(m.beats) == 8
    assert m.notes == 4 + 8 and m.soundfont == "t.sf2" and m.tracks == ["lead", "drums"]
    with wave.open(str(m.path)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (2, 48000, 2)
    assert np.abs(m.audio).max() <= 1.0 and np.sqrt((m.audio ** 2).mean()) > 0.05
    assert peak_hz(m.audio[: 48000]) == pytest.approx(440, abs=4)


def test_builtin_bank_and_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    monkeypatch.setenv("FUNK_SOUNDFONT_URL", (tmp_path / "missing").as_uri())
    sc = {"bpm": 100, "bars": 1, "tracks": [{"program": 71, "notes": "C4/4 E4 G4 C5"}]}
    m = score.render(sc)
    assert m.soundfont == "builtin" and "could not be downloaded" in m.warnings[0]
    names = [n for _, _, n in sf2.SoundFont(score.builtin_bank()).presets()]
    assert "Clarinet" in names and "Drums" in names
    assert peak_hz(m.audio[: int(0.55 * 48000)]) == pytest.approx(261.6, abs=6)


def test_a_reel_can_use_a_score_for_music_and_beats(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from funkbox.reel import audio
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    p = tmp_path / "song.music.json"
    p.write_text(json.dumps({"bpm": 90, "bars": 1, "soundfont": "builtin", "tracks": [{"notes": "C4/1"}]}))
    reel = SimpleNamespace(audio=SimpleNamespace(music=str(p), bpm=None, beat_offset=0.0), base_dir=tmp_path)
    wav = audio.music_file(reel)
    assert wav.suffix == ".wav" and wav.parent.name == "music" and wav.is_file()
    assert audio.music_file(reel) == wav                                            # cached by content
    grid = audio.beat_grid_for(reel)
    assert grid[:2] == [0.0, pytest.approx(0.6667, abs=1e-3)]
