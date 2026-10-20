"""v3.1 P7: publication hygiene (no real brand, no personal data, no secrets, no big files)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True).stdout.decode().split("\0")
    return [ROOT / p for p in out if p and (ROOT / p).is_file()]


def _text(p):
    try:
        return p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scan(patterns, skip=()):
    rx = re.compile(patterns, re.I)
    bad = []
    for p in tracked():
        if p.name in skip:
            continue
        t = _text(p)
        if t is not None:
            bad += [f"{p.relative_to(ROOT)}:{i}" for i, line in enumerate(t.splitlines(), 1) if rx.search(line)]
    return bad


def test_no_file_over_5mb():
    big = [(str(p.relative_to(ROOT)), p.stat().st_size) for p in tracked() if p.stat().st_size > 5 * 1024 * 1024]
    assert not big, big


def test_gallery_is_small():
    files = [p for p in (ROOT / "docs" / "gallery").iterdir() if p.is_file()]
    assert files and all(p.stat().st_size <= 2 * 1024 * 1024 for p in files), files
    assert sum(p.stat().st_size for p in files) <= 6 * 1024 * 1024


def test_no_secrets():
    rx = (r"sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
          r"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|"
          r"BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9/+_-]{20,}['\"]")
    assert not _scan(rx, skip=("test_p7_hygiene.py",))


def test_no_real_brand_or_personal_data():
    brand = "|".join(["x-?" + "tol", "xtol" + "clean", "caro" + "lyn"])
    personal = "|".join(["Users\\\\" + "Zvi", "zvis" + "laptop", "colum" + "bia", "spl13", "Landsman"])
    assert not _scan(brand + "|" + personal, skip=("test_p7_hygiene.py",))


def test_no_sandbox_or_user_home_paths():
    assert not _scan(r"/home/claude|/root/|C:\\Users\\|/Users/[a-z]", skip=("test_p7_hygiene.py",))


def test_license_notices_and_gitignore():
    assert "Copyright (c) 2026 funkbox contributors" in (ROOT / "LICENSE").read_text()
    n = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for needle in ("webgl-noise", "Oswald", "DM Serif Display", "Depth-Anything-V2-Small", "ljspeech", "piper-tts",
                   "FastSD CPU", "gimp-mcp", "GIMP", "GPL-3.0", "Apache-2.0"):
        assert needle in n, needle
    gi = (ROOT / ".gitignore").read_text().split()
    for entry in ("out/", "inbox/", "projects/", "vendor/", "models/", ".venv/", "*.mp4", "*.gif", "*.wav",
                  "funkbox.toml", "funkbox/studio/GIMP-MCP.cmd"):
        assert entry in gi, entry


def test_readme_is_public_facing():
    r = (ROOT / "README.md").read_text()
    assert "AGENTS.md" in r and "docs/gallery/" in r and "OpenGL 3.3" in r and "MIT" in r
    assert len(r.splitlines()) < 200
    for d in ("loops.md", "reel.md", "studio.md"):
        assert (ROOT / "docs" / d).is_file() and f"docs/{d}" in r
