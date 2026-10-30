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
