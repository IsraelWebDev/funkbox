"""Fonts by name, and the check that every character has a glyph.

A font setting (brand.font_caption, brand.font_display, brand.font_fallback, in reels and posters) is either a path to
a .ttf/.otf file, as before, or a NAME:

    "Rubik"            regular (400)
    "Rubik:800"        weight 800
    "Exo 2:700:italic" weight 700, italic

A name is looked up in Google Fonts (github.com/google/fonts; only the OFL, Apache and UFL folders, so every font is
free to use and ship), downloaded once into the font cache (FUNK_HOME/fonts-cache, licence file alongside) and used
from there offline afterwards. A variable font is cut to the requested weight, so the result is always one plain
font file. FUNK_FONTS_URL points the lookup at a mirror (tests use a local folder).

missing() lists the characters a font has no glyph for. funkbox refuses to render text in a font that lacks any
of its characters, so "exact text" never comes out as empty boxes.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.request
from functools import lru_cache
from pathlib import Path

FONT_EXT = (".ttf", ".otf", ".ttc")
LICENCE_DIRS = ("ofl", "apache", "ufl")
DEFAULT_URL = "https://raw.githubusercontent.com/google/fonts/main"
_NAME_RE = re.compile(r"^(?P<family>[A-Za-z0-9][A-Za-z0-9 ]*?)(?::(?P<weight>\d{3}))?(?::(?P<style>italic|normal))?$")


class FontError(ValueError):
    pass


def is_name(spec: str) -> bool:
    """True for a font name ("Rubik:800"), False for a path ("fonts/x.ttf", "brand/fonts/y.otf")."""
    s = str(spec).strip()
    return not ("/" in s or "\\" in s or s.lower().endswith(FONT_EXT)) and bool(_NAME_RE.match(s))


def parse(spec: str) -> tuple[str, int, str]:
    m = _NAME_RE.match(str(spec).strip())
    if not m:
        raise FontError(f"font {spec!r}: expected a file path or a name like 'Rubik', 'Rubik:800' or 'Exo 2:700:italic'")
    return m["family"].strip(), int(m["weight"] or 400), m["style"] or "normal"


def cache_dir() -> Path:
    from . import config
    d = config.get().home / "fonts-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached_name(family: str, weight: int, style: str) -> str:
    return f"{family.replace(' ', '')}-{weight}{'-italic' if style == 'italic' else ''}.ttf"


def _get(url: str) -> bytes:
    if url.startswith("file://"):
        return Path(urllib.request.url2pathname(url[7:])).read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "funkbox"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _metadata(family: str) -> tuple[str, str, dict]:
    """(base url of the family folder, licence dir, parsed METADATA.pb) for a Google Fonts family."""
    base = os.environ.get("FUNK_FONTS_URL", DEFAULT_URL).rstrip("/")
    folder = re.sub(r"[^a-z0-9]", "", family.lower())
    errors = []
    for lic in LICENCE_DIRS:
        url = f"{base}/{lic}/{folder}"
        try:
            text = _get(f"{url}/METADATA.pb").decode("utf-8")
        except OSError as e:
            errors.append(str(e))
            continue
        fonts = [dict(re.findall(r'(\w+):\s*"?([^"\n]*)"?', blk)) for blk in re.findall(r"fonts\s*\{(.*?)\}", text, re.S)]
        axes = {a: (float(lo), float(hi)) for a, lo, hi in
                re.findall(r'axes\s*\{\s*tag:\s*"(\w+)"\s*min_value:\s*([\d.]+)\s*max_value:\s*([\d.]+)', text)}
        name = re.search(r'^name:\s*"([^"]+)"', text, re.M)
        return url, lic, {"name": name.group(1) if name else family, "fonts": fonts, "axes": axes}
    offline = all("urlopen" in e or "Errno" in e or "resolve" in e.lower() for e in errors)
    raise FontError(f"font {family!r} is not in Google Fonts (looked in {', '.join(LICENCE_DIRS)})"
                    + (" or there is no network to look it up; use a font file path instead" if offline else ""))


def fetch(spec: str) -> Path:
    """Download (once) and return a static font file for a font name."""
    family, weight, style = parse(spec)
    out = cache_dir() / _cached_name(family, weight, style)
    if out.is_file():
        return out
    url, lic, meta = _metadata(family)
    files = [f for f in meta["fonts"] if f.get("style", "normal") == style] or meta["fonts"]
    if not files:
        raise FontError(f"font {family!r}: no font files listed")
    variable = [f for f in files if "[" in f.get("filename", "")]
    if variable:
        blob = _get(f"{url}/{variable[0]['filename']}")
        lo, hi = meta["axes"].get("wght", (weight, weight))
        if not lo <= weight <= hi:
            raise FontError(f"font {family!r}: weight {weight} is outside its range {lo:.0f}-{hi:.0f}")
        _instance(blob, out, {"wght": weight} if "wght" in meta["axes"] else {})
    else:
        best = min(files, key=lambda f: abs(int(f.get("weight", 400)) - weight))
        if int(best.get("weight", 400)) != weight:
            raise FontError(f"font {family!r} has no weight {weight}; it has "
                            f"{sorted({int(f.get('weight', 400)) for f in files})}")
        out.write_bytes(_get(f"{url}/{best['filename']}"))
    for lic_file in ("OFL.txt", "LICENSE.txt", "UFL.txt"):
        try:
            (cache_dir() / f"{family.replace(' ', '')}-{lic_file}").write_bytes(_get(f"{url}/{lic_file}"))
            break
        except OSError:
            continue
    (cache_dir() / "index.json").write_text(json.dumps(
        {**_index(), out.name: {"family": meta["name"], "weight": weight, "style": style, "licence": lic,
                                "source": url}}, indent=2) + "\n", encoding="utf-8")
    return out


def _index() -> dict:
    try:
        return json.loads((cache_dir() / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _instance(blob: bytes, out: Path, axes: dict) -> None:
    import io
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    f = TTFont(io.BytesIO(blob))
    if "fvar" in f:
        pinned = {t: v for t, v in axes.items() if any(a.axisTag == t for a in f["fvar"].axes)}
        others = {a.axisTag: a.defaultValue for a in f["fvar"].axes if a.axisTag not in pinned}
        f = instancer.instantiateVariableFont(f, {**others, **pinned})
    out.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(out))


def resolve(spec: str, base_dir: str | Path | None = None) -> Path:
    """A font setting to a font file: names are fetched/cached, paths resolved like every other recipe path."""
    if is_name(spec):
        return fetch(spec)
    from . import config
    try:
        return config.resolve_path(spec, base_dir)
    except FileNotFoundError:
        # The fonts that ship with funkbox ("fonts/Oswald-SemiBold.ttf", ...) live in the checkout, which is not
        # FUNK_HOME when the data folder is elsewhere (the Claude Code plugin uses ~/funkbox-data).
        rel = str(spec).replace("\\", "/")
        bundled = config.get().root / rel
        if rel.startswith("fonts/") and "/" not in rel[6:] and bundled.is_file():
            return bundled
        raise


# --------------------------------------------------------------------------- coverage
@lru_cache(maxsize=32)
def _cmap(path: str) -> frozenset[int]:
    from fontTools.ttLib import TTFont
    f = TTFont(path, fontNumber=0, lazy=True)
    return frozenset(f.getBestCmap() or {})


def _needs_glyph(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return not (ch.isspace() or cat in ("Cc", "Cf", "Zs", "Zl", "Zp"))


def missing(path: str | Path, text: str) -> list[str]:
    """Characters of ``text`` that ``path`` has no glyph for, in order of first appearance."""
    have = _cmap(str(path))
    out: list[str] = []
    for ch in text:
        if _needs_glyph(ch) and ord(ch) not in have and ch not in out:
            out.append(ch)
    return out


def describe(chars: list[str]) -> str:
    return ", ".join(f"{c!r} (U+{ord(c):04X} {unicodedata.name(c, '?').title()})" for c in chars[:8]) + (
        f" and {len(chars) - 8} more" if len(chars) > 8 else "")
