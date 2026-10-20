"""`reel_cli.py setup-models`: the ONLY place that downloads anything (plain HTTPS, no token)."""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

from .depth import models_dir, models_manifest


def _log(*a, **k) -> None:
    print(*a, file=sys.stderr, **k)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def status() -> dict[str, dict]:
    """Per model: installed? hash ok?"""
    out = {}
    man = models_manifest()
    for key, m in man.items():
        files = []
        for f in m["files"]:
            p = models_dir() / f["dest"]
            files.append({"file": f["dest"], "present": p.exists(),
                          "hash_ok": p.exists() and sha256_file(p) == f["sha256"]})
        out[key] = {"name": m["name"], "license": m["license"], "files": files,
                    "ready": all(x["hash_ok"] for x in files)}
    return out


def _download(url: str, dest: Path, expect: str, size: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "funk-reel-setup/3"})
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if size:
                _log(f"\r  {dest.name}: {done / 1e6:.0f}/{size / 1e6:.0f} MB", end="", flush=True)
    _log("")
    if h.hexdigest() != expect:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 mismatch for {dest.name}: got {h.hexdigest()}, expected {expect}. "
                           f"File discarded.")
    part.replace(dest)


def setup_models(only: list[str] | None = None, force: bool = False) -> list[dict]:
    """Download missing models, verify SHA-256, print each licence. Returns a report per model."""
    man = models_manifest()
    report = []
    for key, m in man.items():
        if only and key not in only:
            continue
        _log(f"[{key}] {m['name']}")
        for f in m["files"]:
            dest = models_dir() / f["dest"]
            if dest.exists() and not force and sha256_file(dest) == f["sha256"]:
                _log(f"  {f['dest']}: already present, hash OK")
                continue
            _log(f"  downloading {f['url']}")
            _download(f["url"], dest, f["sha256"], f.get("size", 0))
            _log(f"  {f['dest']}: SHA-256 OK ({f['sha256'][:16]}...)")
        _log(f"  LICENCE: {m['license']}")
        _log(f"  {m['license_note']}")
        report.append({"model": key, "license": m["license"], "files": [
            {"file": f["dest"], "sha256": f["sha256"]} for f in m["files"]]})
    return report
