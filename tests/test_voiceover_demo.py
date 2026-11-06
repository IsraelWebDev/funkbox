"""The voiceover demo: its copy deck is hash-locked, and its music score and reel are valid."""

import hashlib
import json
from pathlib import Path

from funkbox.music.score import check

ROOT = Path(__file__).resolve().parents[1] / "examples" / "voiceover_demo"


def test_copy_deck_is_hash_locked():
    lock = (ROOT / "copy.sha256").read_text(encoding="utf-8").split()[0]
    data = (ROOT / "copy.json").read_bytes().replace(b"\r\n", b"\n")  # line endings do not change the deck
    assert hashlib.sha256(data).hexdigest() == lock


def test_bed_score_is_valid():
    score = json.loads((ROOT / "bed.music.json").read_text(encoding="utf-8"))
    assert check(score) == []


def test_demo_reel_has_voice_and_ducked_bed():
    reel = json.loads((ROOT / "demo.reel.json").read_text(encoding="utf-8"))
    assert reel["audio"]["voiceover"] is True
    assert reel["audio"]["music"] == "bed.music.json"
    assert reel["audio"]["music_gain_db"] == -22 and reel["audio"]["duck_db"] == -10


def test_committed_audits_record_kokoro():
    for fmt in ("16x9", "9x16"):
        doc = json.loads((ROOT / "output" / f"final-{fmt}.text_audit.json").read_text(encoding="utf-8"))
        assert doc["ok"] is True
        assert doc["tts"] == {"mode": "kokoro", "voice": "kokoro:af_heart"}
