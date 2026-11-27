"""Spec v2 upgrades: recorded clips with marks (3.1), the PII guard (3.2), the intake and omit list (3.3), the
project style file (3.4), the 0.3 s slack warning (3.5), the frame review sheet (3.6), and a recorded clip with a
speed ramp and a frame push-in in both formats (3.7)."""

import http.server
import json
import subprocess
import threading
from functools import partial
from pathlib import Path

import pytest

from funkbox.media import ffmpeg_exe
from funkbox.reel import guard, marks
from funkbox.reel import project as P
from funkbox.reel.schema import ReelError, parse_reel
from funkbox.reel.timeline import ShotSpan, slack_warnings
from funkbox.reel.validate import validate

HAVE_PLAYWRIGHT = True
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as _p:
        _b = _p.chromium.launch()
        _b.close()
except Exception:  # noqa: BLE001 - no browser here: the live-recording tests are skipped
    HAVE_PLAYWRIGHT = False


# ---------- helpers ---------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _folders(tmp_path, monkeypatch):
    """Every path a test writes or reads lives under tmp_path, inside the folders funkbox allows."""
    monkeypatch.setenv("FUNK_HOME", str(tmp_path))
    for k in ("FUNK_INPUTS", "FUNK_OUT", "FUNK_PROJECTS", "FUNK_INBOX"):
        monkeypatch.setenv(k, str(tmp_path / k.split("_", 1)[1].lower()))


def _write_marks(clip: Path, data: dict) -> None:
    clip.with_name(clip.stem + ".marks.json").write_text(json.dumps(data), encoding="utf-8")


def _empty_copy(tmp: Path) -> str:
    """A copy deck with one line (a deck must have at least one)."""
    (tmp / "copy.json").write_text(json.dumps({"lines": {"t": {"text": "Hi"}}}), encoding="utf-8")
    return "copy.json"


def _copy(tmp: Path, lines: dict) -> str:
    p = tmp / "copy.json"
    p.write_text(json.dumps({"lines": lines}), encoding="utf-8")
    return "copy.json"


def _reel_dict(copy: str, shots: list) -> dict:
    return {"version": 3, "copy": copy, "shots": shots}


def _color_shot(sid: str, copy_id: str | None = None, dur: float = 1.0) -> dict:
    sh = {"id": sid, "source": {"color": "#202020"}, "duration": dur}
    if copy_id:
        sh["caption"] = {"copy_id": copy_id, "style": "red-box"}
    return sh


def _synthetic_clip(path: Path, seconds: float = 3.0, w: int = 540, h: int = 960) -> None:
    ff = ffmpeg_exe()
    if not ff:
        pytest.skip("no ffmpeg")
    codec = ["-c:v", "libvpx", "-b:v", "300k"] if path.suffix == ".webm" else ["-c:v", "mpeg4", "-q:v", "6"]
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={w}x{h}:rate=24:duration={seconds}",
            *codec,
            str(path),
        ],
        check=True,
    )


# ---------- 3.1 marks ---------------------------------------------------------- #
def test_edge_accepts_seconds_and_marks_and_rejects_the_rest():
    errs: list[str] = []
    assert marks.parse_edge(1.5, "x", errs) == ("time", 1.5)
    assert marks.parse_edge({"mark": "result", "offset_ms": 300}, "x", errs) == ("mark", "result", 0.3)
    assert marks.parse_edge({"mark": "result"}, "x", errs) == ("mark", "result", 0.0)
    assert errs == []
    assert marks.parse_edge({"mark": "Bad Name"}, "x", errs) is None
    assert marks.parse_edge({"mark": "a", "extra": 1}, "x", errs) is None
    assert marks.parse_edge(-1, "x", errs) is None
    assert marks.parse_edge(True, "x", errs) is None
    assert len(errs) == 4


def test_resolve_trim_uses_marks_and_offsets(tmp_path):
    clip = tmp_path / "scene.webm"
    clip.write_bytes(b"x")
    _write_marks(clip, {"name-entered": 0.5, "result": 1.2})
    errs: list[str] = []
    trim = marks.resolve_trim(clip, ("mark", "name-entered", 0.0), ("mark", "result", 0.3), "shots[s1]", errs)
    assert errs == [] and trim == (0.5, 1.5)


def test_changing_the_recording_moves_the_cut_to_the_same_mark(tmp_path):
    """Acceptance 2: a re-recording with a different pause moves the marks; the reel's marks follow without edits."""
    clip = tmp_path / "scene.webm"
    clip.write_bytes(b"x")
    errs: list[str] = []
    _write_marks(clip, {"name-entered": 0.5, "result": 1.2})
    before = marks.resolve_trim(clip, ("mark", "name-entered", 0.0), ("mark", "result", 0.0), "s", errs)
    _write_marks(clip, {"name-entered": 0.5, "result": 1.9})  # pause_ms changed -> the result moved later
    after = marks.resolve_trim(clip, ("mark", "name-entered", 0.0), ("mark", "result", 0.0), "s", errs)
    assert errs == [] and before == (0.5, 1.2) and after == (0.5, 1.9)


def test_missing_mark_and_missing_marks_file_are_named_not_guessed(tmp_path):
    clip = tmp_path / "scene.webm"
    clip.write_bytes(b"x")
    errs: list[str] = []
    assert marks.resolve_trim(clip, ("mark", "a", 0), ("mark", "b", 0), "s", errs) is None
    assert any("no scene.marks.json" in e for e in errs)
    _write_marks(clip, {"a": 1.0})
    errs.clear()
    assert marks.resolve_trim(clip, ("mark", "a", 0), ("mark", "nope", 0), "s", errs) is None
    assert any("mark 'nope' is not in" in e for e in errs)


def test_out_must_come_after_in(tmp_path):
    clip = tmp_path / "scene.webm"
    clip.write_bytes(b"x")
    _write_marks(clip, {"a": 2.0, "b": 1.0})
    errs: list[str] = []
    assert marks.resolve_trim(clip, ("mark", "a", 0), ("mark", "b", 0), "s", errs) is None
    assert any("out must come after in" in e for e in errs)


def test_a_shot_with_marks_resolves_to_its_trim(tmp_path):
    clip = tmp_path / "inputs" / "login.webm"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    _write_marks(clip, {"name-entered": 0.5, "result": 1.2})
    d = {
        "version": 3,
        "copy": "copy.json",
        "shots": [
            {
                "id": "s1",
                "source": {"video": "inputs/login.webm"},
                "in": {"mark": "name-entered"},
                "out": {"mark": "result", "offset_ms": 300},
            }
        ],
    }
    reel = parse_reel(d, str(tmp_path))
    assert reel.shots[0].source.trim == (0.5, 1.5)


def test_scene_file_is_checked_before_the_browser_starts(tmp_path):
    bad = tmp_path / "scene.json"
    bad.write_text(
        json.dumps(
            {
                "url": "ftp://example.test",
                "steps": [
                    {"do": "jump"},
                    {"do": "click", "selector": "#a", "mark": "m"},
                    {"do": "click", "selector": "#b", "mark": "m"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(marks.MarksError) as e:
        marks.load_scene(bad)
    msg = str(e.value)
    assert "url:" in msg and "expected one of" in msg and "duplicate mark name" in msg


def test_recording_is_off_unless_asked(tmp_path):
    scene = tmp_path / "scene.json"
    scene.write_text(json.dumps({"url": "http://127.0.0.1:9/", "steps": [{"do": "goto", "url": "/"}]}), "utf-8")
    with pytest.raises(marks.MarksError, match="off by default"):
        marks.record_scene(scene, tmp_path, enabled=False)


@pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="needs Playwright and Chromium")
def test_two_step_scene_records_and_its_marks_validate(tmp_path):
    """Acceptance 1: a two-step scene records, writes marks, and a shot defined by those marks validates."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        "<!doctype html><body><input id='name'><button id='go' "
        "onclick=\"document.getElementById('m').textContent='Saved'\">Submit</button><p id='m'></p></body>",
        encoding="utf-8",
    )
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
        scene = tmp_path / "login.scene.json"
        scene.write_text(
            json.dumps(
                {
                    "url": url,
                    "steps": [
                        {
                            "do": "fill",
                            "selector": "#name",
                            "text": "Alex Example",
                            "pause_ms": 300,
                            "mark": "name-entered",
                        },
                        {"do": "click", "selector": "#go", "pause_ms": 200, "mark": "submitted"},
                        {"do": "wait_for", "text": "Saved", "mark": "result"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        res = marks.record_scene(scene, tmp_path / "inputs", enabled=True)
    finally:
        srv.shutdown()
    clip = Path(res["clip"])
    assert clip.exists() and clip.stat().st_size > 0
    got = json.loads(Path(res["marks"]).read_text(encoding="utf-8"))
    assert set(got) == {"name-entered", "submitted", "result"}
    assert got["name-entered"] < got["submitted"] < got["result"]
    d = {
        "version": 3,
        "copy": "copy.json",
        "shots": [
            {
                "id": "s1",
                "source": {"video": f"inputs/{clip.name}"},
                "in": {"mark": "name-entered"},
                "out": {"mark": "result"},
            }
        ],
    }
    _empty_copy(tmp_path)
    rep = validate(d, str(tmp_path), deep=False)
    assert rep.ok, rep.errors
    assert rep.reel.shots[0].source.trim[1] > rep.reel.shots[0].source.trim[0]


# ---------- 3.2 PII guard ------------------------------------------------------- #
def test_forbidden_string_fails_and_is_not_printed(tmp_path):
    (tmp_path / "project.pii.json").write_text(
        json.dumps({"forbidden": ["Alex Example"], "patterns": [{"name": "ssn", "regex": r"\d{3}-\d{2}-\d{4}"}]}),
        encoding="utf-8",
    )
    cp = _copy(tmp_path, {"hook": {"text": "Hi ALEX EXAMPLE"}, "ok": {"text": "Fine"}, "id": {"text": "123-45-6789"}})
    d = _reel_dict(cp, [_color_shot("s1", "hook", 1.5)])
    rep = validate(d, str(tmp_path), deep=False)
    assert not rep.ok
    joined = "\n".join(rep.errors)
    assert "copy line 'hook' matches forbidden entry #1" in joined
    assert "Alex Example" not in joined and "ALEX" not in joined
    # rules report the copy id and the rule name only (the matching line 'id' is not a shot's line here)
    d2 = _reel_dict(cp, [_color_shot("s1", "id", 1.5)])
    rep2 = validate(d2, str(tmp_path), deep=False)
    assert any("matches rule 'ssn'" in e for e in rep2.errors)
    assert "123-45-6789" not in "\n".join(rep2.errors)


def test_pii_file_is_bad_json_rejected(tmp_path):
    (tmp_path / "project.pii.json").write_text('{"forbidden": [1]}', encoding="utf-8")
    with pytest.raises(guard.GuardError, match="forbidden"):
        guard.load_pii(tmp_path)


def test_new_project_git_ignores_the_pii_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(tmp_path / "inbox"))
    P.new_project("Pii Check")
    gi = (tmp_path / "projects" / "pii-check" / ".gitignore").read_text(encoding="utf-8")
    assert gi.count("project.pii.json") == 1


# ---------- 3.3 intake and omit ------------------------------------------------- #
def test_intake_is_checked(tmp_path):
    good = {
        "audience": "customer training",
        "length_s": 20,
        "omit": ["draft"],
        "sources": ["footage"],
        "names": [],
        "voiceover": {"enabled": False},
    }
    assert guard.validate_intake(good) == []
    bad = dict(good, length_s=2, sources=["cloud"], extra=1)
    errs = guard.validate_intake(bad)
    assert (
        any("length_s" in e for e in errs) and any("sources" in e for e in errs) and any("unknown" in e for e in errs)
    )
    with pytest.raises(guard.GuardError):
        guard.write_intake(tmp_path, bad)


def test_omit_entry_fails_every_shot_that_matches(tmp_path):
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    guard.write_intake(
        tmp_path,
        {
            "audience": "internal",
            "length_s": 12,
            "omit": ["vintage"],
            "sources": ["footage"],
            "names": [],
            "voiceover": {"enabled": False},
        },
    )
    cp = _copy(tmp_path, {"hook": {"text": "A VINTAGE LOOK"}, "ok": {"text": "A new look"}})
    rep = validate(_reel_dict(cp, [_color_shot("s1", "ok"), _color_shot("s2", "hook")]), str(tmp_path), deep=False)
    assert not rep.ok
    assert "shot 's2' matches intake omit entry #1" in rep.errors
    assert not any("'s1'" in e for e in rep.errors)


# ---------- 3.4 style file ------------------------------------------------------ #
def test_style_file_is_created_and_corrections_are_dated(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(tmp_path / "inbox"))
    P.new_project("Style Check")
    text = P.style_text("style-check")
    assert "Write for end users" in text and "## Corrections" in text
    assert P.correction_count("style-check") == 0
    line = P.add_correction("style-check", "Say 'sign in', not 'authenticate'.")
    assert line.startswith("- 20") and line.endswith("'sign in', not 'authenticate'.")
    assert P.correction_count("style-check") == 1
    with pytest.raises(P.ProjectError):
        P.add_correction("style-check", "x" * 301)


# ---------- 3.5 slack ----------------------------------------------------------- #
def test_slack_under_point_three_warns_with_the_shot_id():
    def span(sid, dur, vo):
        return ShotSpan(id=sid, kind="shot", index=0, start=0.0, end=dur, start_frame=0, end_frame=1, vo_dur=vo)

    w = slack_warnings([span("s1", 2.0, 1.8), span("s2", 3.0, 1.0), span("s3", 2.0, None), span("s4", 1.0, 1.1)])
    assert len(w) == 1 and w[0].startswith("shot s1: 0.20s of slack")


# ---------- 3.6 review sheet ---------------------------------------------------- #
def test_review_sheet_writes_a_contact_sheet_of_each_shot(tmp_path):
    from funkbox.reel import review

    d = _reel_dict("copy.json", [_color_shot("s1", None, 1.0), _color_shot("s2", None, 1.0)])
    _empty_copy(tmp_path)
    reel_path = tmp_path / "demo.reel.json"
    reel_path.write_text(json.dumps(d), encoding="utf-8")
    (tmp_path / "out").mkdir()
    _synthetic_clip(tmp_path / "out" / "demo.mp4", seconds=2.0, w=540, h=960)
    dest = review.review_sheet(reel_path)
    assert dest.exists() and dest.name == "demo-review.png"
    from PIL import Image

    with Image.open(dest) as im:
        assert im.width > 300 and im.height > 300


def test_review_needs_a_render_first(tmp_path):
    from funkbox.reel import review

    _empty_copy(tmp_path)
    reel_path = tmp_path / "demo.reel.json"
    reel_path.write_text(json.dumps(_reel_dict("copy.json", [_color_shot("s1")])), encoding="utf-8")
    with pytest.raises(review.ReviewError, match="render the reel first"):
        review.review_sheet(reel_path)


# ---------- 3.7 recorded clip with speed ramp and frame push-in, both formats ---- #
def test_recorded_clip_with_speed_ramp_and_push_in_validates_in_both_formats(tmp_path):
    clip = tmp_path / "inputs" / "login.webm"
    clip.parent.mkdir()
    _synthetic_clip(clip, seconds=3.0, w=540, h=960)
    _write_marks(clip, {"start": 0.2, "end": 2.2})
    _empty_copy(tmp_path)
    d = {
        "version": 3,
        "copy": "copy.json",
        "formats": ["9x16", "16x9"],
        "shots": [
            {
                "id": "s1",
                "source": {"video": "inputs/login.webm", "speed": [1, 3]},
                "motion": {"type": "frame", "to": [0.2, 0.3, 0.6, 0.4], "at": [0.0, 1.0], "ease": "out"},
                "in": {"mark": "start"},
                "out": {"mark": "end"},
                "duration": 1.5,
            }
        ],
    }
    rep = validate(d, str(tmp_path))
    assert rep.ok, rep.errors
    assert rep.reel.shots[0].source.trim == (0.2, 2.2)
    assert rep.reel.shots[0].source.speed == (1.0, 3.0)


# ---------- the new text stays honest and the old checks are unchanged ---------- #
def test_shot_with_in_but_no_video_source_is_refused(tmp_path):
    d = {
        "version": 3,
        "copy": "copy.json",
        "shots": [{"id": "s1", "source": {"color": "#000000"}, "in": 1.0, "out": 2.0}],
    }
    _empty_copy(tmp_path)
    with pytest.raises(ReelError, match="in/out need a video source"):
        parse_reel(d, str(tmp_path))
