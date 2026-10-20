import os
import sys
from pathlib import Path

if sys.platform == "linux" and "DISPLAY" not in os.environ:
    os.environ.setdefault("FUNK_GL_BACKEND", "egl")

os.environ.setdefault("STUDIO_FAKE_SD", "1")         # no stills test ever starts FastSD
os.environ.setdefault("REEL_DEPTH", "stub")
os.environ.setdefault("REEL_TTS", "stub")

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def gl():
    from funkbox.gl.context import GLUnavailable, create_context
    try:
        return create_context()
    except GLUnavailable as e:
        pytest.skip(str(e))


@pytest.fixture
def work(request):
    """A scratch folder inside FUNK_OUT (the path fence only allows FUNK_HOME/OUT/INPUTS/PROJECTS)."""
    import re
    import shutil

    from funkbox.config import funk_out
    d = funk_out() / "test_work_stills" / re.sub(r"[^A-Za-z0-9_-]", "_", request.node.name)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d
