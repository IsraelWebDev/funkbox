"""The shared timeline editor: plain-words diffs, validated saves with backup and log, conflicts, thumbnails,
external edits picked up, the page itself."""

import json
import shutil
import time
import urllib.error
import urllib.request

import pytest

from funkbox.reel import editor


def test_describe_changes_in_plain_words():
    old = {
        "shots": [
            {"id": "a", "duration": "beats:1"},
            {"id": "b", "duration": 1.0, "source": {"video": "v.mp4", "trim": [1, 2]}},
            {"id": "c"},
            {"id": "d"},
        ],
        "audio": {"bpm": 100},
    }
    new = {
        "shots": [
            {"id": "b", "duration": 1.0, "source": {"video": "v.mp4", "trim": [1.5, 2]}},
            {"id": "a", "duration": "beats:2"},
            {"id": "e", "source": {"image": "x.jpg"}},
            {"id": "c"},
        ],
        "audio": {"bpm": 110},
    }
    ch = editor.describe_changes(old, new)
    assert "deleted 'd'" in ch and "added 'e' at position 3 (x.jpg)" in ch
    assert "moved 'b' from position 2 to 1" in ch
    assert '\'a\': duration "beats:1" -> "beats:2"' in ch and "'b': source.trim [1, 2] -> [1.5, 2]" in ch
    assert "audio changed" in ch
    assert editor.describe_changes(old, old) == []


@pytest.fixture()
def reel_file(demo, work):
    """A copy of the demo reel and its assets in a scratch folder."""
    d = work / "tl"
    shutil.copytree(demo, d)
    raw = json.loads((d / "demo.reel.json").read_text())
    raw["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    raw["audio"] = {"bpm": 120, "voiceover": False}
    for s in raw["shots"]:
        s.pop("vo", None)
        if s.get("duration") == "auto":
            s["duration"] = "beats:2"
    p = d / "demo.reel.json"
    p.write_text(json.dumps(raw, indent=2))
    return p


def http(url, data=None):
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read(), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type")


def pumped(ed, fn):
    """Run ``fn`` (HTTP calls) on a thread while this thread, which owns the GL context, runs the editor's GL work."""
    import threading

    box = {}

    def go():
        try:
            box["ok"] = fn()
        except BaseException as e:
            box["err"] = e

    t = threading.Thread(target=go)
    t.start()
    while t.is_alive():
        ed.pump.pump(0.05)
    if "err" in box:
        raise box["err"]
    return box.get("ok")


def test_the_editor_serves_saves_validates_and_logs(reel_file):
    ed = editor.Editor(reel_file).start()
    try:
        pumped(ed, lambda: _scenario(ed, reel_file))
    finally:
        ed.stop()


def _scenario(ed, reel_file):
    if True:
        code, body, ctype = http(ed.url)
        assert code == 200 and ctype.startswith("text/html") and b"Drag a shot" in body
        code, body, _ = http(ed.url + "api/state")
        st = json.loads(body)
        ids = [s["id"] for s in st["reel"]["shots"]]
        assert code == 200 and st["beat"] == pytest.approx(0.5) and not st["errors"]
        assert [s["id"] for s in st["spans"] if s["kind"] == "shot"] == ids
        code, jpg, ctype = http(ed.url + f"api/thumb?id={ids[0]}")
        assert code == 200 and ctype == "image/jpeg" and jpg[:2] == b"\xff\xd8", jpg[:300]

        new = json.loads(json.dumps(st["reel"]))
        new["shots"].insert(0, new["shots"].pop(2))  # drag the third shot to the front
        new["shots"][1]["duration"] = "beats:3"
        code, body, _ = http(ed.url + "api/save", {"base_mtime": st["mtime"], "reel": new})
        st2 = json.loads(body)
        assert code == 200 and json.loads(reel_file.read_text())["shots"][0]["id"] == ids[2]
        assert reel_file.with_name("demo.reel.json.bak").is_file()
        log = [json.loads(l) for l in ed.log_path.read_text().splitlines()]
        assert log[-1]["by"] == "timeline" and f"moved '{ids[2]}' from position 3 to 1" in log[-1]["changes"]

        stale = {"base_mtime": st["mtime"], "reel": new}  # saving on an old version: refused
        code, body, _ = http(ed.url + "api/save", stale)
        assert code == 409

        bad = json.loads(json.dumps(st2["reel"]))
        bad["shots"][0]["caption"] = {"copy_id": "no-such-line"}
        before = reel_file.read_text()
        code, body, _ = http(ed.url + "api/save", {"base_mtime": st2["mtime"], "reel": bad})
        assert code == 422 and "no-such-line" in json.dumps(json.loads(body)) and reel_file.read_text() == before

        # Claude edits the file directly: the page sees a new mtime and the log says what changed
        time.sleep(0.05)
        raw = json.loads(reel_file.read_text())
        raw["shots"].pop()
        reel_file.write_text(json.dumps(raw, indent=2))
        code, body, _ = http(ed.url + "api/changed")
        assert json.loads(body)["mtime"] != st2["mtime"]
        st3 = json.loads(http(ed.url + "api/state")[1])
        assert st3["edits"][-1]["by"] == "file" and st3["edits"][-1]["changes"][0].startswith("deleted")
        text = editor.changes_text(reel_file)
        assert "you (timeline)" in text and "file changed outside the timeline" in text

        code, _, _ = http(ed.url + "media/../../secret.mp4")
        assert code == 404


def test_cross_origin_posts_are_refused(reel_file):
    ed = editor.Editor(reel_file).start()
    try:
        req = urllib.request.Request(ed.url + "api/save", data=b"{}", headers={"Origin": "http://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 403
    finally:
        ed.stop()


def test_only_reel_files(work):
    p = work / "x.json"
    p.write_text("{}")
    with pytest.raises(ValueError, match="reel.json"):
        editor.Editor(p)
