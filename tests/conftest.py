import os
import sys
from pathlib import Path

if sys.platform == "linux" and "DISPLAY" not in os.environ:
    os.environ.setdefault("FUNK_GL_BACKEND", "egl")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def gl():
    from funkgl.context import GLUnavailable, create_context
    try:
        return create_context()
    except GLUnavailable as e:
        pytest.skip(str(e))
