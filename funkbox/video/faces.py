"""Where the faces are in a video frame, so the subtitles can keep off them.

    faces_in(rgb) -> [(x0, y0, x1, y1), ...]   in the frame's own pixels

Faces are found with OpenCV's Haar cascade (haarcascade_frontalface_default.xml). The cascade ships inside the
opencv-python-headless 4.x wheel, which OpenCV licenses under Apache-2.0, and needs no model download. It is older
than the neural detectors: it misses faces turned far to one side and some faces in poor light. So a frame with no
face found is not a promise that there is none; the subtitle audit records the count it saw.

Not used: YuNet. Its training lineage includes WIDER FACE, which is licensed for non-commercial use only.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from funkbox.video.clip import VideoError

_DETECT_WIDTH = 480  # faces are searched on a copy this wide: quicker, and a Haar cascade does not need more
_CASCADE = None
_INSTALL = "pip install -r requirements-transcribe.txt"


def _cv2():
    try:
        import cv2
    except ImportError:
        raise VideoError(f"face detection needs OpenCV: {_INSTALL}") from None
    return cv2


def _detector():
    global _CASCADE
    if _CASCADE is None:
        cv2 = _cv2()
        xml = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not xml.exists():
            raise VideoError(f"this OpenCV build has no face cascade (OpenCV 5 dropped it): {_INSTALL}")
        det = cv2.CascadeClassifier(str(xml))
        if det.empty():
            raise VideoError(f"the face cascade {xml.name} did not load")
        _CASCADE = det
    return _CASCADE


def faces_in(rgb: np.ndarray) -> list[tuple[float, float, float, float]]:
    """The face boxes of one RGB frame, in that frame's own pixels. Raises VideoError when OpenCV is not installed."""
    cv2 = _cv2()
    det = _detector()
    rgb = np.ascontiguousarray(rgb[..., :3], dtype=np.uint8)
    h, w = rgb.shape[:2]
    scale = min(1.0, _DETECT_WIDTH / max(1, w))
    if scale < 1.0:
        rgb = cv2.resize(rgb, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    found = det.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    return [(x / scale, y / scale, (x + fw) / scale, (y + fh) / scale) for x, y, fw, fh in found]


def overlaps(box: tuple[float, float, float, float], faces) -> bool:
    """True when a rectangle (x0, y0, x1, y1) touches any face box."""
    x0, y0, x1, y1 = box
    return any(x0 < fx1 and fx0 < x1 and y0 < fy1 and fy0 < y1 for fx0, fy0, fx1, fy1 in faces)
