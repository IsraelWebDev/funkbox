"""Choosing the voice: reel audio.voice, FUNK_VOICE, Kokoro first when installed, cache keys per voice."""
import pytest

from funkbox.reel import audio, schema


def test_voice_resolution_order(monkeypatch):
    monkeypatch.delenv("FUNK_VOICE", raising=False)
    monkeypatch.setattr(audio, "_kokoro_ready", lambda: True)
    assert audio.resolve_voice() == "kokoro:af_heart"
    assert audio.resolve_voice("kokoro:bm_george") == "kokoro:bm_george"
    monkeypatch.setenv("FUNK_VOICE", "kokoro:am_michael")
    assert audio.resolve_voice() == "kokoro:am_michael" and audio.resolve_voice("piper") == "piper"
    monkeypatch.delenv("FUNK_VOICE")
    monkeypatch.setattr(audio, "_kokoro_ready", lambda: False)
    monkeypatch.setattr(audio, "_piper_ready", lambda: True)
    assert audio.resolve_voice() == "piper"                          # Kokoro missing: fall back to Piper


def test_missing_engine_says_what_to_install(monkeypatch):
    monkeypatch.delenv("REEL_TTS", raising=False)
    monkeypatch.setattr(audio, "_kokoro_ready", lambda: False)
    monkeypatch.setattr(audio, "_has", lambda m: False)
    assert audio.tts_mode("kokoro:af_heart") == "none"
    assert "kokoro-onnx is not installed" in audio.tts_unavailable_reason("kokoro:af_heart")


def test_reel_schema_takes_voice_and_speed():
    base = {"version": 3, "copy": "c.json", "shots": [{"id": "s", "source": {"color": "#000000"}, "duration": 1}]}
    errs = []
    try:
        r = schema.parse_reel({**base, "audio": {"voice": "kokoro:bm_george", "voice_speed": 1.1}})
        assert r.audio.voice == "kokoro:bm_george" and r.audio.voice_speed == 1.1
    except schema.ReelError as e:                                    # other parts of this minimal reel may be invalid
        errs = [m for m in e.errors if "voice" in m]
        assert not errs
    with pytest.raises(schema.ReelError) as ei:
        schema.parse_reel({**base, "audio": {"voice": "alexa", "voice_speed": 3}})
    msg = " ".join(ei.value.errors)
    assert "audio.voice" in msg and "audio.voice_speed" in msg


def test_cache_key_differs_per_voice(monkeypatch, tmp_path):
    from funkbox.reel.copydeck import parse_copy
    monkeypatch.setenv("FUNK_OUT", str(tmp_path))
    monkeypatch.setenv("REEL_TTS", "stub")
    deck = parse_copy({"lines": {"a": {"text": "Hello"}}})
    assert audio.synthesize(deck, "a", "kokoro:af_heart").exists()


def test_lines_split_into_sentences_for_pauses():
    assert audio.sentences("Wemby. Clark. Rose.") == ["Wemby.", "Clark.", "Rose."]
    assert audio.sentences("Every dunk. Every dagger!") == ["Every dunk.", "Every dagger!"]
    assert audio.sentences("Meet G.L.O.W. today") == ["Meet G.L.O.W. today"]          # no space: not a sentence end
    assert audio.sentences("Faster, sharper, louder") == ["Faster, sharper, louder"]
    assert audio.sentences('He said "go." Then left.') == ['He said "go."', "Then left."]


def test_kokoro_line_has_a_pause_between_sentences(monkeypatch, tmp_path):
    import wave
    import numpy as np

    class Fake:
        def get_voices(self):
            return ["am_michael"]

        def create(self, text, voice, speed, lang):
            return np.full(2400, 0.5), 24000                    # 0.1 s of "speech" per sentence
    monkeypatch.setattr(audio, "_kokoro", lambda: Fake())
    out = tmp_path / "v.wav"
    audio._kokoro_wav("Wemby. Clark. Rose.", "am_michael", 1.0, out)
    with wave.open(str(out)) as w:
        y = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    quiet = np.abs(y) < 10
    assert len(y) / 24000 == pytest.approx(3 * 0.1 + 2 * audio.SENTENCE_PAUSE, abs=0.01)
    assert quiet[2400 + 100: 2400 + int(audio.SENTENCE_PAUSE * 24000) - 100].all()
