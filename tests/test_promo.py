"""promo/: the review gate, the posting gates (no network), X request signing, and the asset helpers."""
import json
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "promo"))

import post  # noqa: E402
import review  # noqa: E402

GOOD_CAP = {"youtube": {"title": "Captions that match your approved copy", "description": "d", "tags": ["funkbox"]},
            "x": {"text": "Made with funkbox. IsraelWebDev/funkbox on GitHub #AIvideo"}}


@pytest.fixture
def day(tmp_path, monkeypatch):
    d = tmp_path / "2026-10-29"
    d.mkdir()
    (d / "final.mp4").write_bytes(b"not really a video")
    (d / "caption.json").write_text(json.dumps(GOOD_CAP))
    monkeypatch.setattr(post, "LOG", tmp_path / "posts.jsonl")
    monkeypatch.setattr(post, "PAUSE", tmp_path / "PAUSED")
    monkeypatch.setattr(post, "video_problems", lambda p: [])
    for k in post.YT_SECRETS + post.X_SECRETS + ("PROMO_PAUSED",):
        monkeypatch.delenv(k, raising=False)
    return d


def test_review_is_bound_to_the_exact_bytes(day):
    assert "no review.json" in review.check(day)
    review.write(day, "PASS", "reviewer agent", "looked at 6 frames")
    assert review.check(day) is None
    (day / "caption.json").write_text(json.dumps({**GOOD_CAP, "x": {"text": "changed after review"}}))
    assert "changed after the review" in review.check(day)
    review.write(day, "FAIL", "reviewer agent", "title overclaims")
    assert "FAIL" in review.check(day)
    with pytest.raises(SystemExit):
        review.write(day, "PASS", "", "")


def test_post_refuses_without_pass_and_obeys_the_kill_switch(day, capsys):
    assert post.run(day, ["youtube", "x"]) == 2
    review.write(day, "PASS", "reviewer agent", "ok")
    post.PAUSE.write_text("")
    assert post.run(day, ["youtube", "x"]) == 3
    post.PAUSE.unlink()
    assert post.run(day, ["youtube", "x"]) == 0               # no secrets: both platforms dry-run
    out = capsys.readouterr().out
    assert out.count("DRY RUN") == 2 and not post.LOG.exists()


def test_one_post_per_platform_per_day(day, monkeypatch, capsys):
    review.write(day, "PASS", "reviewer agent", "ok")
    for k in post.YT_SECRETS:
        monkeypatch.setenv(k, "x")
    calls = []
    monkeypatch.setitem(post.PLATFORMS, "youtube",
                        (post.YT_SECRETS, lambda v, c: calls.append(v) or {"id": "abc", "url": "https://youtube.com/shorts/abc"}))
    assert post.run(day, ["youtube"], today="2026-10-29") == 0
    assert post.run(day, ["youtube"], today="2026-10-29") == 0
    assert len(calls) == 1 and "already posted today" in capsys.readouterr().out
    log = [json.loads(l) for l in post.LOG.read_text().splitlines()]
    assert log[0]["ok"] and log[0]["url"].endswith("abc") and log[0]["video_sha256"] == review.sha256(day / "final.mp4")
    assert post.run(day, ["youtube"], today="2026-10-30") == 0 and len(calls) == 2


def test_failures_are_logged_and_exit_nonzero(day, monkeypatch):
    review.write(day, "PASS", "reviewer agent", "ok")
    for k in post.X_SECRETS:
        monkeypatch.setenv(k, "x")

    def boom(v, c):
        raise post.PostError("X POST /2/tweets failed (402)")
    monkeypatch.setitem(post.PLATFORMS, "x", (post.X_SECRETS, boom))
    assert post.run(day, ["x"], today="2026-10-29") == 1
    e = json.loads(post.LOG.read_text())
    assert e["ok"] is False and "402" in e["error"]
    assert post.posted_today("x", "2026-10-29") is None           # a failed post does not use up the day


@pytest.mark.parametrize("text, bad", [
    ("Free and open source: IsraelWebDev/funkbox on GitHub", False),
    ("see https://github.com/IsraelWebDev/funkbox", True),
    ("see github.com/IsraelWebDev/funkbox", True),
    ("try funkbox.dev today", True),
    ("x" * 281, True),
])
def test_x_text_rules(text, bad):
    probs = post.caption_problems({**GOOD_CAP, "x": {"text": text}})
    assert bool(probs) == bad, probs


def test_youtube_caption_rules():
    assert post.caption_problems({**GOOD_CAP, "youtube": {**GOOD_CAP["youtube"], "title": "a" * 101}})
    assert post.caption_problems({**GOOD_CAP, "youtube": {**GOOD_CAP["youtube"], "description": "use <b>"}})


def test_oauth1_signature_matches_the_published_example():
    """The worked example from X's (Twitter's) "Creating a signature" documentation."""
    h = post.oauth1_header(
        "POST", "https://api.twitter.com/1.1/statuses/update.json",
        {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
        "xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp="1318622958")
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h


def test_music_is_seeded_and_the_right_length(tmp_path):
    import music
    a = music.write(tmp_path / "a.wav", 6.0, 110, 3)
    b = music.write(tmp_path / "b.wav", 6.0, 110, 3)
    c = music.write(tmp_path / "c.wav", 6.0, 110, 4)
    assert a.read_bytes()[44:] == b.read_bytes()[44:] != c.read_bytes()[44:]
    with wave.open(str(a)) as w:
        assert abs(w.getnframes() / w.getframerate() - 6.0) < 0.01


def test_code_cards_show_real_lines(tmp_path):
    import assets
    first, lines = assets.find_lines(ROOT / "examples/mimic/copy.json", '"lines": {', 4)
    real = (ROOT / "examples/mimic/copy.json").read_text(encoding="utf-8").splitlines()
    assert lines == real[first - 1:first + 3]
    out, span = assets.code_card(ROOT / "examples/mimic/copy.json", '"lines": {', 4, tmp_path / "c.png", "copy.json")
    assert out.is_file() and span == f"{first}-{first + 3}"
    for cid, rel, marker, n, *_ in assets.CARDS:                    # every card's marker still exists
        assets.find_lines(ROOT / rel, marker, n)
    with pytest.raises(ValueError):
        assets.find_lines(ROOT / "README.md", "no such marker 123", 2)


def test_playbook_and_review_reference_real_commands():
    pb = (ROOT / "promo" / "PLAYBOOK.md").read_text()
    for needle in ("promo/assets.py", "promo/post.py", "promo/REVIEW.md", "SEPARATE agent", "[skip ci]",
                   "promo/PAUSED", "FACTS.md"):
        assert needle in pb, needle
    tpl = json.loads((ROOT / "promo" / "templates" / "promo.reel.json").read_text())
    assert tpl["audio"]["bpm"] == 110 and tpl["settings"]["height"] == 1920
