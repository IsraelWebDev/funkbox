import json

import pytest

from funkbox.reel.schema import ReelError, parse_reel


def test_demo_assets_and_schema(demo):
    reel = parse_reel(json.loads((demo / "demo.reel.json").read_text()), str(demo))
    assert len(reel.shots) == 12 and reel.end_card is not None
    assert (demo / "inputs/brand/bottle.png").exists()
    assert (demo / "inputs/music/click120.wav").exists()


def test_unknown_key_rejected():
    with pytest.raises(ReelError) as e:
        parse_reel({"copy": "c.json", "shots": [{"source": {"color": "#000000"}, "wat": 1}]})
    assert "shots[0].wat: unknown key" in str(e.value)
