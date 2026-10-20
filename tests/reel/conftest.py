import os
import sys
from pathlib import Path

if sys.platform == "linux" and "DISPLAY" not in os.environ:
    os.environ.setdefault("FUNK_GL_BACKEND", "egl")

os.environ.setdefault("REEL_DEPTH", "stub")      # tests never run the ONNX models unless asked
os.environ.setdefault("REEL_TTS", "stub")

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "examples" / "reel_demo"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def gl():
    from funkgl.context import GLUnavailable, create_context
    try:
        return create_context()
    except GLUnavailable as e:
        pytest.skip(str(e))


@pytest.fixture(scope="session")
def demo(tmp_path_factory, gl):
    """Synthetic demo assets at 270x480 inside FUNK_HOME/out/ (the only writable place)."""
    import make_assets
    from funkgl.recipe import funk_out
    d = funk_out() / "test_demo_assets"
    make_assets.make(d, 270, 480)
    return d


@pytest.fixture
def work(request):
    """A scratch folder inside FUNK_OUT (the path fence only allows FUNK_HOME/OUT/INPUTS)."""
    import re
    import shutil

    from funkgl.recipe import funk_out
    d = funk_out() / "test_work" / re.sub(r"[^A-Za-z0-9_-]", "_", request.node.name)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def make_plain_reel(demo, **over):
    """The demo reel with voiceover/music off and fixed durations (no audio needed)."""
    import json
    d = json.loads((demo / "demo.reel.json").read_text())
    d["audio"] = {"voiceover": False}
    for sh in d["shots"]:
        sh["duration"] = 1.0
        sh.pop("vo", None)
    d.update(over)
    return d
