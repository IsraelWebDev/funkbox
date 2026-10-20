import shutil
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from funkgl import Engine, load_recipe, render_recipe
from funkgl.audio import SPEC_BINS, analyze, read_wav, wav_duration
from funkgl.recipe import Layer, Recipe2, Settings2


def write_wav(path: Path, samples: np.ndarray, sr: int = 22050, channels: int = 1) -> Path:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def sine(freq, secs=2.0, sr=22050, amp=0.6):
    t = np.arange(int(secs * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t)


def test_100hz_sine_is_bass_in_every_frame(tmp_path):
    wav = write_wav(tmp_path / "s100.wav", sine(100))
    assert abs(wav_duration(wav) - 2.0) < 1e-3
    f = analyze(wav, fps=10, n_frames=20)
    assert f.bands.shape == (21, 4) and f.spectrum.shape == (21, SPEC_BINS)
    bass, mid, treble = f.bands[:, 0], f.bands[:, 1], f.bands[:, 2]
    assert np.all(bass > treble), (bass, treble)
    assert f.bands.min() >= 0.0 and f.bands.max() <= 1.0
    # the spectrum row peaks near 100 Hz on the 20..16k log axis
    expect = np.log(100 / 20) / np.log(16000 / 20) * (SPEC_BINS - 1)
    assert abs(int(np.argmax(f.spectrum[10])) - expect) < 8


def test_5khz_sine_is_treble(tmp_path):
    f = analyze(write_wav(tmp_path / "s5k.wav", sine(5000)), fps=10, n_frames=20)
    assert np.all(f.bands[:, 2] > f.bands[:, 0])


def test_stereo_and_bad_file(tmp_path):
    x, sr = read_wav(write_wav(tmp_path / "st.wav", sine(440, 0.5), channels=2))
    assert x.ndim == 1 and sr == 22050 and abs(len(x) - 11025) <= 1
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    with pytest.raises(ValueError, match="PCM WAV"):
        read_wav(bad)


REACT = """vec4 effect(vec2 p) {
  return vec4(vec3(iAudio.x) * (0.4 + 0.6 * texture(iChannel3, vec2(0.25, 0.5)).r), 1.0);
}
"""


def test_audio_drives_shader_and_muxes_into_mp4(gl, tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    quiet = np.zeros(11025)
    loud = sine(100, 1.0)
    wav = write_wav(tmp_path / "mix.wav", np.concatenate([quiet, loud[:22050 - 11025 + 11025]])[:44100])
    rec = Recipe2.from_dict({"settings": {"width": 32, "height": 32, "fps": 10, "audio": "mix.wav"},
                             "layers": [{"type": "shader", "source": REACT}]})
    assert abs(rec.settings.seconds - wav_duration(wav)) < 1e-3          # seconds defaults to audio length
    e = Engine(32, 32, gl)
    try:
        first = e.render(rec, 1).mean()       # silent part
        later = e.render(rec, rec.settings.frames - 3).mean()   # loud part
    finally:
        e.release()
    assert first < 5 and later > 40, (first, later)
    res = render_recipe(rec, tmp_path / "o", "aud", gif=False)
    ff = shutil.which("ffprobe")
    if ff:
        r = subprocess.run([ff, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            res.paths["mp4"]], capture_output=True, text=True, check=True)
        assert "audio" in r.stdout and "video" in r.stdout
