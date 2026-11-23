"""Subtitles keep off faces: the anchor "auto" reads the frame and the face finder's boxes.

The end-to-end tests stub the face finder, so they run without OpenCV. The last test runs the real Haar cascade and
is skipped unless OpenCV 4.x (which ships the cascade files) is installed.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from funkbox.video import clip, faces
from funkbox.video.clip import VideoError

FIXTURE = Path(__file__).parent / "data" / "speech_en.mp4"
CUES = [{"start": 1.0, "end": 3.0, "text": "together with family"}]


def _has_cascade() -> bool:
    if importlib.util.find_spec("cv2") is None:
        return False
    import cv2

    return (Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml").exists()


needs_cascade = pytest.mark.skipif(not _has_cascade(), reason="OpenCV 4.x with its face cascade is not installed")


@pytest.fixture()
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    return tmp_path


def _face_over_the_bottom_of_every_frame(rgb):
    h, w = rgb.shape[:2]
    return [(0.0, 0.6 * h, float(w), float(h))]


def test_a_box_touching_a_face_is_blocked_and_touching_an_edge_is_not():
    face = (100.0, 100.0, 200.0, 200.0)
    assert faces.overlaps((150, 150, 300, 300), [face])
    assert not faces.overlaps((0, 0, 100, 100), [face])  # shares an edge only
    assert not faces.overlaps((0, 0, 50, 50), [])


def test_auto_keeps_the_caption_off_a_face(work, monkeypatch):
    monkeypatch.setattr(faces, "faces_in", _face_over_the_bottom_of_every_frame)
    res = clip.subtitles(FIXTURE, CUES, None, "lower-third", "auto", work, "moved")
    cue = json.loads(res.audit.read_text(encoding="utf-8"))[0]
    assert cue["anchor"] in ("top", "center"), cue
    assert cue["faces"]["faces"] == 1 and cue["faces"]["clear"] is True
    assert cue["faces"]["frames"] == clip.FACE_SAMPLES
    assert cue["text_exact"]


def test_a_face_seen_in_one_sampled_frame_keeps_the_caption_off_it(work, monkeypatch):
    seen = []

    def only_the_second_frame_has_a_face(rgb):
        seen.append(1)
        return _face_over_the_bottom_of_every_frame(rgb) if len(seen) == 2 else []

    monkeypatch.setattr(faces, "faces_in", only_the_second_frame_has_a_face)
    res = clip.subtitles(FIXTURE, CUES, None, "lower-third", "auto", work, "one-frame")
    cue = json.loads(res.audit.read_text(encoding="utf-8"))[0]
    assert cue["anchor"] in ("top", "center"), cue
    assert cue["faces"]["faces"] == 1 and cue["faces"]["clear"] is True


def test_sample_times_span_the_caption_and_stay_inside_it():
    times = clip._sample_times(2.0, 4.0)
    assert len(times) == clip.FACE_SAMPLES
    assert 2.0 < times[0] < times[-1] < 4.0
    assert times == sorted(times)
    assert clip._sample_times(2.0, 2.2) == [2.1] or all(2.0 < t < 2.2 for t in clip._sample_times(2.0, 2.2))


def test_no_face_found_is_unknown_not_clear(work, monkeypatch):
    monkeypatch.setattr(faces, "faces_in", lambda rgb: [])
    res = clip.subtitles(FIXTURE, CUES, None, "lower-third", "auto", work, "none-seen")
    cue = json.loads(res.audit.read_text(encoding="utf-8"))[0]
    assert cue["faces"]["faces"] == 0 and cue["faces"]["clear"] is None
    assert cue["anchor"] == "bottom" and "kept at bottom" in cue["faces"]["why"]


def test_auto_says_so_when_the_face_finder_is_missing(work, monkeypatch):
    def no_opencv(rgb):
        raise VideoError("face detection needs OpenCV")

    monkeypatch.setattr(faces, "faces_in", no_opencv)
    res = clip.subtitles(FIXTURE, CUES, None, "lower-third", "auto", work, "fallback")
    cue = json.loads(res.audit.read_text(encoding="utf-8"))[0]
    assert cue["anchor"] == "bottom"  # the old default, and the audit says the faces were not checked
    assert cue["faces"]["clear"] is None and "OpenCV" in cue["faces"]["why"]
    assert cue["text_exact"]


def test_a_fixed_anchor_is_kept_and_not_checked(work, monkeypatch):
    monkeypatch.setattr(faces, "faces_in", _face_over_the_bottom_of_every_frame)
    res = clip.subtitles(FIXTURE, CUES, None, "lower-third", "top", work, "fixed")
    cue = json.loads(res.audit.read_text(encoding="utf-8"))[0]
    assert cue["anchor"] == "top" and cue["faces"] is None


def test_an_unknown_anchor_is_refused(work):
    with pytest.raises(VideoError, match="anchor: auto, top, center or bottom"):
        clip.subtitles(FIXTURE, CUES, None, "lower-third", "left", work)


@needs_cascade
def test_the_real_cascade_finds_no_face_in_a_blank_frame():
    assert faces.faces_in(np.zeros((360, 640, 3), dtype=np.uint8)) == []
