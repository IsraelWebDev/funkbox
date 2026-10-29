"""promo/: the review gate, the posting gates (no network), the Buffer calls (faked), and the asset helpers."""
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
    monkeypatch.setattr(post, "VIDEO_URL_BASE", "")                 # no public copy unless a test sets one
    for k in post.YT_SECRETS + ("BUFFER_API_KEY", "BUFFER_AUTH", "BUFFER_ORG_ID", "PROMO_PAUSED", "YT_AUTH"):
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
    monkeypatch.setattr(post, "publish",
                        lambda p, v, c, l, u=None: calls.append(v) or {"id": "abc", "url": "https://youtube.com/shorts/abc"})
    assert post.run(day, ["youtube"], today="2026-10-29") == 0
    assert post.run(day, ["youtube"], today="2026-10-29") == 0
    assert len(calls) == 1 and "already posted" in capsys.readouterr().out
    other = day.parent / "2026-10-29-b"                                # a second promo the same UTC day: refused
    other.mkdir()
    for f in ("final.mp4", "caption.json"):
        (other / f).write_bytes((day / f).read_bytes())
    review.write(other, "PASS", "reviewer agent", "ok")
    assert post.run(other, ["youtube"], today="2026-10-29") == 0
    assert len(calls) == 1 and "already posted today" in capsys.readouterr().out
    log = [json.loads(l) for l in post.LOG.read_text().splitlines()]
    assert log[0]["ok"] and log[0]["url"].endswith("abc") and log[0]["video_sha256"] == review.sha256(day / "final.mp4")
    assert post.run(day, ["youtube"], today="2026-10-30") == 0 and len(calls) == 1   # same promo, next day: no repost
    assert "already posted" in capsys.readouterr().out


def test_failures_are_logged_and_exit_nonzero(day, monkeypatch):
    review.write(day, "PASS", "reviewer agent", "ok")
    monkeypatch.setenv("BUFFER_API_KEY", "k")
    post.LOG.write_text(json.dumps({"date": "2026-10-29", "platform": "youtube", "ok": True, "day": day.name,
                                    "url": "https://youtube.com/shorts/abc"}) + "\n")

    def boom(p, v, c, l, u=None):
        raise post.PostError("Buffer refused the post (402)")
    monkeypatch.setattr(post, "publish", boom)
    assert post.run(day, ["x"], today="2026-10-29") == 1
    e = json.loads(post.LOG.read_text().splitlines()[-1])
    assert e["ok"] is False and "402" in e["error"]
    assert post.posted_today("x", "2026-10-29") is None           # a failed post does not use up the day


def test_youtube_proxy_mode_sends_no_credentials(day, monkeypatch, capsys):
    for k, v in zip(post.YT_SECRETS, ("cid", "csec", "rtok")):
        monkeypatch.setenv(k, v)
    direct = dict(__import__("urllib.parse").parse.parse_qsl(post.youtube_token_body().decode()))
    assert direct == {"grant_type": "refresh_token", "client_id": "cid", "client_secret": "csec", "refresh_token": "rtok"}
    monkeypatch.setenv("YT_AUTH", "proxy")
    assert post.youtube_token_body() == b"grant_type=refresh_token"   # even with the variables still set
    for k in post.YT_SECRETS:
        monkeypatch.delenv(k)
    review.write(day, "PASS", "reviewer agent", "ok")
    calls = []
    monkeypatch.setattr(post, "publish",
                        lambda p, v, c, l, u=None: calls.append(v) or {"id": "p", "url": "https://youtube.com/shorts/p"})
    assert post.run(day, ["youtube"], today="2026-10-29") == 0 and len(calls) == 1   # configured without the variables


@pytest.mark.parametrize("text, bad", [
    ("Free and open source: IsraelWebDev/funkbox on GitHub", False),
    ("see https://github.com/IsraelWebDev/funkbox", True),
    ("see github.com/IsraelWebDev/funkbox", True),
    ("try funkbox.dev today", True),
    ("x" * 256, True),                                            # 255 + the 23-char link would pass 280
])
def test_x_text_rules(text, bad):
    probs = post.caption_problems({**GOOD_CAP, "x": {"text": text}})
    assert bool(probs) == bad, probs


def test_youtube_caption_rules():
    assert post.caption_problems({**GOOD_CAP, "youtube": {**GOOD_CAP["youtube"], "title": "a" * 101}})
    assert post.caption_problems({**GOOD_CAP, "youtube": {**GOOD_CAP["youtube"], "description": "use <b>"}})


class FakeBuffer:
    """Answers Buffer GraphQL calls by what the query asks for; records every request."""
    def __init__(self, status="sent", chans=None):
        self.reqs, self.status = [], status
        self.chans = chans if chans is not None else [{"id": "c-bsky", "service": "bluesky", "isQueuePaused": False},
                                                      {"id": "c-x", "service": "twitter", "isQueuePaused": False}]

    def __call__(self, method, url, *, data=None, headers=None, timeout=0):
        body = json.loads(data)
        self.reqs.append({"url": url, "headers": headers, **body})
        q = body["query"]
        if "organizations" in q:
            d = {"account": {"organizations": [{"id": "org1"}]}}
        elif "channels" in q:
            d = {"channels": self.chans}
        elif "createPost" in q:
            d = {"createPost": {"post": {"id": "p1", "status": "sending", "externalLink": None}}}
        else:
            d = {"post": {"id": "p1", "status": self.status, "externalLink": "https://x.com/funkbox/status/9"
                          if self.status == "sent" else None, "error": {"message": "channel disconnected"}}}
        return 200, {}, json.dumps({"data": d}).encode()


def test_x_goes_through_buffer_after_youtube(day, monkeypatch, capsys):
    review.write(day, "PASS", "reviewer agent", "ok")
    monkeypatch.setenv("BUFFER_API_KEY", "secret-key")
    monkeypatch.setattr(post.time, "sleep", lambda s: None)
    fake = FakeBuffer()
    monkeypatch.setattr(post, "_http", fake)
    assert post.run(day, ["x"], today="2026-10-29") == 0                     # no YouTube post yet: nothing sent
    assert "no YouTube post to link" in capsys.readouterr().out and not fake.reqs
    post.LOG.write_text(json.dumps({"date": "2026-10-28", "platform": "youtube", "ok": True, "day": day.name,
                                    "url": "https://youtube.com/shorts/abc"}) + "\n")   # yesterday's upload counts
    assert post.run(day, ["x"], today="2026-10-29") == 0
    create = next(r for r in fake.reqs if "createPost" in r["query"])["variables"]["input"]
    assert create["channelId"] == "c-x" and create["mode"] == "shareNow"
    assert create["text"] == GOOD_CAP["x"]["text"] + "\n\nhttps://youtube.com/shorts/abc"
    assert all(r["headers"]["Authorization"] == "Bearer secret-key" for r in fake.reqs)
    assert json.loads(post.LOG.read_text().splitlines()[-1])["url"] == "https://x.com/funkbox/status/9"


def test_buffer_proxy_mode_and_failures(day, monkeypatch):
    monkeypatch.setenv("BUFFER_AUTH", "proxy")
    monkeypatch.setattr(post.time, "sleep", lambda s: None)
    fake = FakeBuffer()
    monkeypatch.setattr(post, "_http", fake)
    assert post.secrets_for("x") == ()
    post.buffer_post("bluesky", GOOD_CAP, "https://youtube.com/shorts/abc")
    assert all("Authorization" not in r["headers"] for r in fake.reqs)       # the proxy adds it
    assert next(r for r in fake.reqs if "createPost" in r["query"])["variables"]["input"]["channelId"] == "c-bsky"
    monkeypatch.setattr(post, "_http", FakeBuffer(status="error"))
    with pytest.raises(post.PostError, match="channel disconnected"):
        post.buffer_post("x", GOOD_CAP, "l")
    monkeypatch.setattr(post, "_http", FakeBuffer(chans=[]))
    with pytest.raises(post.PostError, match="no twitter channel"):
        post.buffer_post("x", GOOD_CAP, "l")


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
    out, span, box = assets.code_card(ROOT / "examples/mimic/copy.json", '"lines": {', 4, tmp_path / "c.png", "copy.json")
    assert out.is_file() and span == f"{first}-{first + 3}"
    assert 0 < box[0] < box[2] <= 1 and 0 < box[1] < box[3] <= assets.TEXT_BOTTOM
    _, span, box = assets.code_card(ROOT / "README.md", "# funkbox", 400, tmp_path / "long.png", "README.md")
    shown = int(span.split("-")[1]) - int(span.split("-")[0]) + 1
    assert 1 <= shown < 400 and box[3] <= assets.TEXT_BOTTOM               # a long file is cut, never runs down
    for cid, rel, marker, n, *_ in assets.CARDS:                    # every card's marker still exists
        assets.find_lines(ROOT / rel, marker, n)
    with pytest.raises(ValueError):
        assets.find_lines(ROOT / "README.md", "no such marker 123", 2)


def test_playbook_and_review_reference_real_commands():
    pb = (ROOT / "promo" / "PLAYBOOK.md").read_text()
    for needle in ("promo/assets.py", "promo/post.py", "promo/REVIEW.md", "SEPARATE agent", "promo/state.py", "$STATE/days", "promo/lint.py", "promo/measure.py",
                   "feedback.json", "novelty",
                   "promo/PAUSED", "FACTS.md"):
        assert needle in pb, needle
    tpl = json.loads((ROOT / "promo" / "templates" / "promo.reel.json").read_text())
    assert tpl["audio"]["bpm"] == 110 and tpl["settings"]["height"] == 1920


@pytest.fixture
def pending(day, monkeypatch, tmp_path):
    """pending.py against a real (local, bare, empty) git repo standing in for the private one."""
    import subprocess
    import pending as pend
    bare = tmp_path / "pending.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    import state
    monkeypatch.setattr(state, "STATE_URL", bare.as_uri())
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "clone")
    monkeypatch.setattr(state, "DAYS", day.parent)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t"); monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@e"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@e")
    return pend


def test_saved_video_is_posted_by_the_next_run(day, pending, monkeypatch):
    pend = pending
    with pytest.raises(pend.PendingError, match="review"):          # only reviewed videos are kept
        pend.save(day)
    review.write(day, "PASS", "reviewer agent", "ok")
    pend.save(day)
    assert pend.open_store().days() == ["2026-10-29"]
    import state
    import subprocess
    other = day.parent / "elsewhere"                                # pushed: another machine's clone has it
    subprocess.run(["git", "clone", "-q", state.STATE_URL, str(other)], check=True)
    assert (other / "2026-10-29.mp4").read_bytes() == (day / "final.mp4").read_bytes()
    assert "days/*/final.mp4" in (state.STATE_DIR / ".gitignore").read_text()
    original = (day / "final.mp4").read_bytes()
    (day / "final.mp4").unlink()                                   # the run died: its container is gone
    posted = []
    monkeypatch.setattr(post, "publish", lambda p, v, c, l, u=None: posted.append((p, v.read_bytes(), l)) or
                        {"id": p, "url": f"https://example/{p}"})
    for k in ("YT_AUTH", "BUFFER_AUTH"):
        monkeypatch.setenv(k, "proxy")
    notes = pend.catchup(today="2026-10-30")
    assert posted == [("youtube", original, None), ("x", original, "https://example/youtube")], notes
    assert pend.open_store().days() == []                          # posted, so the saved copy is gone
    assert pend.catchup(today="2026-10-30") == ["nothing owed"]


def test_catchup_finishes_x_without_the_video_and_drops_stale_days(day, pending, monkeypatch):
    pend = pending
    review.write(day, "PASS", "reviewer agent", "ok")
    sha = review.sha256(day / "final.mp4")
    (day / "final.mp4").unlink()
    post.LOG.write_text(json.dumps({"date": "2026-10-29", "platform": "youtube", "day": day.name, "ok": True,
                                    "url": "https://youtube.com/shorts/abc", "video_sha256": sha}) + "\n")
    posted = []
    monkeypatch.setattr(post, "publish", lambda p, v, c, l, u=None: posted.append((p, l)) or {"id": "1", "url": "u"})
    monkeypatch.setenv("BUFFER_AUTH", "proxy")
    pend.catchup(today="2026-10-30")
    assert posted == [("x", "https://youtube.com/shorts/abc")]
    assert json.loads(post.LOG.read_text().splitlines()[-1])["video_sha256"] == sha
    # a video saved 3 days ago is dropped, not posted
    old = day.parent / "2026-10-26"
    old.mkdir()
    (old / "final.mp4").write_bytes(b"old video")
    pend.open_store().put("2026-10-26", old / "final.mp4")
    notes = pend.catchup(today="2026-10-29")
    assert any("dropped without posting" in n for n in notes) and pend.open_store().days() == []


def test_x_attaches_the_video_when_its_public_copy_matches(day, monkeypatch, capsys):
    review.write(day, "PASS", "reviewer agent", "ok")
    monkeypatch.setenv("BUFFER_AUTH", "proxy")
    monkeypatch.setattr(post.time, "sleep", lambda s: None)
    monkeypatch.setattr(post, "VIDEO_URL_BASE", "https://raw.example/pending/main/")
    post.LOG.write_text(json.dumps({"date": "2026-10-29", "platform": "youtube", "ok": True, "day": day.name,
                                    "url": "https://youtube.com/shorts/abc"}) + "\n")
    fake, size = FakeBuffer(), str((day / "final.mp4").stat().st_size)

    def http(method, url, **kw):
        if method == "HEAD":
            return (200, {"Content-Length": size}, b"") if url.endswith(f"{day.name}.mp4") else (404, {}, b"")
        return fake(method, url, **kw)
    monkeypatch.setattr(post, "_http", http)
    assert post.run(day, ["x"], today="2026-10-29") == 0
    sent = next(r for r in fake.reqs if "createPost" in r["query"])["variables"]["input"]
    assert sent["assets"] == [{"video": {"url": f"https://raw.example/pending/main/{day.name}.mp4"}}]
    assert sent["text"].endswith("\n\nhttps://github.com/IsraelWebDev/funkbox")          # the repo, not YouTube
    assert json.loads(post.LOG.read_text().splitlines()[-1])["media"] == "video"
    # a copy of another size (stale or partial) is not used
    size = "1"
    assert post.public_video_url(day.name, day / "final.mp4") is None


def test_x_falls_back_to_the_link_when_buffer_refuses_the_video(day, monkeypatch, capsys):
    calls = []

    def bp(p, cap, link, video_url=None):
        calls.append(video_url)
        if video_url:
            raise post.PostError("Buffer refused the post: unsupported media")
        return {"id": "1", "url": "u", "media": "link"}
    monkeypatch.setattr(post, "buffer_post", bp)
    assert post.publish("x", day / "final.mp4", GOOD_CAP, "https://youtube.com/shorts/abc", "https://v")["media"] == "link"
    assert calls == ["https://v", None] and "posting the link instead" in capsys.readouterr().out


def test_a_replaced_post_no_longer_counts(day, monkeypatch):
    post.LOG.write_text(json.dumps({"date": "2026-10-29", "platform": "x", "day": day.name, "ok": True,
                                    "url": "u1", "replaced": "deleted by the owner"}) + "\n")
    assert post.posted_day("x", day.name) is None and post.posted_today("x", "2026-10-29") is None


def _brief(d, **kw):
    d.mkdir(parents=True, exist_ok=True)
    (d / "brief.json").write_text(json.dumps({"format": "how-to", "selling_point": "S5", "hook": "one edit four formats",
                                              "novelty": "first how-to", "claims": [{"text": "x", "fact": "F9"}], **kw}))
    (d / "feedback.json").write_text(json.dumps({"observation": "o", "suggestion": "s", "evidence": ["e"]}))
    return d


def test_lint_variety_feedback_and_claims(tmp_path):
    import lint
    days = tmp_path / "days"
    _brief(days / "2026-10-27", format="tip", selling_point="S2", hook="no gpu needed at all")
    _brief(days / "2026-10-28", format="how-to", selling_point="S4", hook="captions exactly as approved")
    today = _brief(days / "2026-10-29", format="how-to", selling_point="S2", hook="NO GPU needed at all, really")
    probs = lint.variety_problems(today)
    assert any("format 'how-to' was used on 2026-10-28" in p for p in probs)
    assert any("selling point S2" in p for p in probs) and any("too close to 2026-10-27" in p for p in probs)
    ok = _brief(days / "2026-10-30", format="behind-the-scenes", selling_point="S11", hook="what changed this week")
    assert lint.variety_problems(ok) == [] and lint.feedback_problems(ok) == []
    (ok / "feedback.json").write_text(json.dumps({"observation": "o"}))
    assert len(lint.feedback_problems(ok)) == 2
    _brief(ok, claims=[{"text": "fast", "fact": "F9 (the formats)"}, {"text": "83 s", "fact": "M-render"},
                       {"text": "best", "fact": "F99"}])
    (ok / "measured.json").write_text(json.dumps({"facts": {"M-render": {"say": "83 s"}}}))
    assert [p for p in lint.claim_problems(ok)] == ["claim 'best' cites ['F99']: use F/L ids from FACTS.md or M-keys "
                                                    "from measured.json"]
    added = tmp_path / "feedback.jsonl"
    assert lint.add_feedback(ok, added) and not lint.add_feedback(ok, added)


def test_lint_catches_a_caption_on_card_text(tmp_path, monkeypatch):
    import lint
    from PIL import Image
    day = tmp_path / "2026-10-29"
    (day / "assets").mkdir(parents=True)
    Image.new("RGB", (1080, 1920), (18, 20, 28)).save(day / "assets" / "card.png")
    (day / "copy.json").write_text(json.dumps({"lines": {"c": {"text": "CAPTIONS MATCH\nYOUR COPY"}}}))
    reel = {"version": 3, "settings": {"width": 1080, "height": 1920, "fps": 30, "safe_area": "reels"},
            "copy": "copy.json", "shots": [{"id": "card", "source": {"image": "assets/card.png"}, "duration": 2,
                                            "caption": {"copy_id": "c", "style": "red-box", "anchor": "bottom"}}]}
    (day / "reel.json").write_text(json.dumps(reel))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))

    def manifest(rect):
        (day / "assets.json").write_text(json.dumps({"assets": [{"path": "assets/card.png", "text_rect": rect}]}))
    manifest([0.02, 0.09, 0.7, 0.56])                                    # text in the top part: clear
    assert lint.caption_problems(day) == []
    manifest([0.02, 0.09, 0.7, 0.75])                                    # text runs down behind the caption
    assert "sits on the card's text" in lint.caption_problems(day)[0]
    manifest([0.02, 0.09, 0.7, 0.56])                                    # a push-in onto the text brings it down
    reel["shots"][0]["motion"] = {"type": "frame", "from": [0, 0, 1, 1], "to": [0.15, 0.05, 0.6, 0.5]}
    (day / "reel.json").write_text(json.dumps(reel))
    assert lint.caption_problems(day)


def test_measured_facts_are_written_with_plain_sentences(tmp_path, monkeypatch):
    import measure
    day = tmp_path / "days" / "2026-10-30"
    day.mkdir(parents=True)
    (day / "final.text_audit.json").write_text(json.dumps({"ok": True, "entries": [{}] * 9}))
    monkeypatch.setattr(measure, "machine", lambda: {"value": {"cores": 2, "gpu": False},
                                                     "say": "a 2-core CPU with no GPU (software OpenGL)"})
    monkeypatch.setattr(measure, "repo_counts", lambda: {"M-tools": {"value": 37, "say": "37 MCP tools"}})
    doc = measure.write(day, render_s=87.4, frames=580)
    f = doc["facts"]
    assert f["M-render"]["say"] == "this video rendered in 87 seconds on a 2-core CPU with no GPU (software OpenGL)"
    assert f["M-audit"]["value"] == {"strings": 9, "ok": True} and "M-video" not in f      # no video, no claim
    assert json.loads((day / "measured.json").read_text())["facts"]["M-tools"]["value"] == 37
