import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _render(out: Path) -> bytes:
    subprocess.run([sys.executable, str(ROOT / "funk.py"), "render", "--preset", "lava-lamp",
                    "--seed", "2", "--seconds", "0.5", "--size", "64", "--no-gif",
                    "--out", str(out)], check=True, stdout=subprocess.DEVNULL, cwd=ROOT)
    return (out / "lava-lamp-s2.png").read_bytes()


def test_v1_png_is_deterministic(tmp_path):
    assert _render(tmp_path / "a") == _render(tmp_path / "b")


def test_funk_py_is_unmodified():
    recorded = (ROOT / "tests" / "v1.sha256").read_text().split()[0]
    assert hashlib.sha256((ROOT / "funk.py").read_bytes()).hexdigest() == recorded
