"""brand.from: colours resolved from an image (or one frame of an mp4) with the reel.brand helpers."""
import subprocess

import pytest
from PIL import Image

from funkbox.reel import brand as B
from funkbox.reel import schema as S


def _striped(path, main, dark=(20, 20, 30), size=(120, 120)):
    """Half the image in `main`, half in a dark tone, so the palette has a dark cluster too."""
    im = Image.new("RGB", size, main)
    im.paste(Image.new("RGB", (size[0], size[1] // 2), dark), (0, size[1] // 2))
    im.save(path)
    return path


def test_blue_image_gives_bluish_primary_with_contrast(tmp_path):
    chosen = B.brand_from_file(_striped(tmp_path / "blue.png", (30, 90, 220)))
    assert chosen is not None
    r, g, b = B.hex_to_rgb(chosen["primary"])
    assert b > r and b > g
    assert B.contrast(chosen["text"], chosen["primary"]) >= 4.5


def test_yellow_image_keeps_vivid_primary_and_dark_text(tmp_path):
    chosen = B.brand_from_file(_striped(tmp_path / "yellow.png", (250, 200, 30)))
    assert chosen is not None
    assert chosen["text"] == "#111111"
    assert B.contrast(chosen["text"], chosen["primary"]) >= 4.5
    r, g, b = B.hex_to_rgb(chosen["primary"])
    assert max(r, g) - min(r, g, b) > 60          # still a vivid colour, not a grey


def test_deterministic(tmp_path):
    p = _striped(tmp_path / "blue.png", (30, 90, 220))
    assert B.brand_from_file(p) == B.brand_from_file(p)


def test_unusable_file_returns_none(tmp_path):
    tiny = tmp_path / "tiny.png"
    Image.new("RGB", (2, 2), (200, 10, 10)).save(tiny)
    assert B.brand_from_file(tiny) is None


def test_missing_file_warns_and_keeps_fallback(tmp_path):
    warnings: list[str] = []
    assert S._resolve_brand_from("nope.png", str(tmp_path), warnings) is None
    assert len(warnings) == 1
    assert "nope.png" in warnings[0]
    assert "keeping the explicit brand colours" in warnings[0]


def test_good_file_adds_no_warning(tmp_path):
    # resolve_path only admits files inside the funkbox folders, so the image goes under the inputs folder
    from funkbox import config as C
    d = C.get().inputs / "test_brand_from" / tmp_path.name
    d.mkdir(parents=True, exist_ok=True)
    try:
        _striped(d / "blue.png", (30, 90, 220))
        warnings: list[str] = []
        chosen = S._resolve_brand_from("blue.png", str(d), warnings)
        assert chosen is not None and warnings == []
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_mp4_frame_works_or_skips(tmp_path):
    from funkbox.media import ffmpeg_exe
    ff = ffmpeg_exe()
    if not ff:                                    # no ffmpeg on this machine
        pytest.skip("ffmpeg not available")
    mp4 = tmp_path / "blue.mp4"
    r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=0x1e5adc:s=240x426:d=1.5", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(mp4)], capture_output=True)
    if r.returncode != 0 or not mp4.exists():
        pytest.skip("could not make a test mp4")
    chosen = B.brand_from_file(mp4)
    assert chosen is not None
    assert B.contrast(chosen["text"], chosen["primary"]) >= 4.5
    assert not list(tmp_path.glob("*.png"))      # the temp frame never lands next to the video
