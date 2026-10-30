"""SVG path data and SVG files, read into flat polygons funkbox can fill, stroke, clip with and export again.

parse(d)            -> list of subpaths, each a list of (x, y) points (curves flattened), and whether each is closed
svg_items(path)     -> the shapes of an SVG file: [{"subpaths", "closed", "fill", "stroke", "stroke_width", "opacity"}]
                       in the file's own units, plus its viewBox. Supports path, rect, circle, ellipse, line,
                       polyline, polygon, groups with transforms, fill/stroke/opacity attributes and style="...".
                       Gradients fall back to their first colour; <text> is skipped with a warning (convert text to
                       outlines in the editor first).
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


class PathError(ValueError):
    pass


def _flatten_cubic(p0, p1, p2, p3, tol):
    n = max(2, min(64, int(math.ceil((math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)) / tol))))
    out = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        out.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return out


def _flatten_arc(p0, rx, ry, phi, large, sweep, p1, tol):
    """SVG elliptical arc (endpoint form) -> points, per the SVG spec's centre parametrisation."""
    if p0 == p1:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [p1]
    cp, sp = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cx1, cy1 = co * rx * y1 / ry, -co * ry * x1 / rx
    cx = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2
    cy = sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2
    ang = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    t1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(2, min(128, int(math.ceil(abs(dt) * max(rx, ry) / tol))))
    out = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((cp * ex - sp * ey + cx, sp * ex + cp * ey + cy))
    out[-1] = p1
    return out


def parse(d: str, tol: float = 0.5) -> list[tuple[list[tuple[float, float]], bool]]:
    """SVG path data -> [(points, closed)]. ``tol`` is the flattening step in the path's own units."""
    toks = _TOKEN.findall(d or "")
    if not toks or not toks[0] in "Mm":
        raise PathError("path data must start with M (moveto)")
    subs: list[tuple[list[tuple[float, float]], bool]] = []
    cur: list[tuple[float, float]] = []
    pos = start = (0.0, 0.0)
    last_ctrl = None
    last_cmd = ""
    i, cmd = 0, ""

    def num() -> float:
        nonlocal i
        if i >= len(toks) or toks[i].isalpha():
            raise PathError(f"path data: command {cmd!r} is missing numbers")
        i += 1
        return float(toks[i - 1])

    def flush(closed: bool) -> None:
        nonlocal cur
        if len(cur) >= 2:
            subs.append((cur, closed))
        cur = []

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
        elif not cmd:
            raise PathError("path data: numbers before any command")
        up, rel = cmd.upper(), cmd.islower()
        if up not in _ARGS:
            raise PathError(f"path data: unknown command {cmd!r}")
        ox, oy = pos if rel else (0.0, 0.0)
        if up == "Z":
            flush(True)
            pos = start
            last_cmd, last_ctrl = "Z", None
            continue
        if up == "M":
            flush(False)
            pos = (ox + num(), oy + num())
            start = pos
            cur = [pos]
            cmd = "l" if rel else "L"                      # further pairs are linetos
        elif up == "L":
            pos = (ox + num(), oy + num())
            cur.append(pos)
        elif up == "H":
            pos = ((pos[0] if rel else 0.0) + num(), pos[1])
            cur.append(pos)
        elif up == "V":
            pos = (pos[0], (pos[1] if rel else 0.0) + num())
            cur.append(pos)
        elif up in "CS":
            if up == "C":
                c1 = (ox + num(), oy + num())
            else:
                c1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_cmd in "CS" and last_ctrl else pos
            c2 = (ox + num(), oy + num())
            end = (ox + num(), oy + num())
            cur += _flatten_cubic(pos, c1, c2, end, tol)
            last_ctrl, pos = c2, end
        elif up in "QT":
            if up == "Q":
                q = (ox + num(), oy + num())
            else:
                q = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_cmd in "QT" and last_ctrl else pos
            end = (ox + num(), oy + num())
            c1 = (pos[0] + 2 / 3 * (q[0] - pos[0]), pos[1] + 2 / 3 * (q[1] - pos[1]))
            c2 = (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1]))
            cur += _flatten_cubic(pos, c1, c2, end, tol)
            last_ctrl, pos = q, end
        elif up == "A":
            rx, ry, phi, large, sweep = num(), num(), num(), num(), num()
            end = (ox + num(), oy + num())
            cur += _flatten_arc(pos, rx, ry, phi, bool(large), bool(sweep), end, tol)
            pos = end
        if up not in "CSQT":
            last_ctrl = None
        last_cmd = up
    flush(False)
    if not subs:
        raise PathError("path data draws nothing")
    return subs


# --------------------------------------------------------------------------- SVG files
_NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
          "yellow": "#ffff00", "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080",
          "navy": "#000080", "gold": "#ffd700", "silver": "#c0c0c0", "maroon": "#800000", "olive": "#808000",
          "teal": "#008080", "lime": "#00ff00", "aqua": "#00ffff", "fuchsia": "#ff00ff", "transparent": None}


def color(v: str | None, defs: dict) -> str | None:
    """An SVG paint -> '#rrggbb', or None for none. url(#grad) -> its first stop colour."""
    if v is None:
        return None
    v = v.strip()
    if v in ("none", ""):
        return None
    if v.startswith("url("):
        ref = re.search(r"#([^)'\"]+)", v)
        return defs.get(ref.group(1)) if ref else None
    if v.lower() in _NAMED:
        return _NAMED[v.lower()]
    if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
        return "#" + "".join(c * 2 for c in v[1:]).lower()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return v.lower()
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)(?:\s*,\s*[\d.]+)?\s*\)", v)
    if m:
        ch = [float(c[:-1]) * 2.55 if c.endswith("%") else float(c) for c in m.groups()]
        return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in ch)
    return "#000000"


def _matrix(tf: str | None) -> tuple[float, ...]:
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", tf or ""):
        a = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", args)]
        if name == "matrix" and len(a) == 6:
            n = tuple(a)
        elif name == "translate":
            n = (1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0)
        elif name == "scale":
            n = (a[0], 0, 0, a[1] if len(a) > 1 else a[0], 0, 0)
        elif name == "rotate":
            r = math.radians(a[0])
            c, s = math.cos(r), math.sin(r)
            n = (c, s, -s, c, 0, 0)
            if len(a) == 3:
                n = _mul(_mul((1, 0, 0, 1, a[1], a[2]), n), (1, 0, 0, 1, -a[1], -a[2]))
        elif name == "skewX":
            n = (1, 0, math.tan(math.radians(a[0])), 1, 0, 0)
        else:
            n = (1, math.tan(math.radians(a[0])), 0, 1, 0, 0)
        m = _mul(m, n)
    return m


def _mul(a, b):
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3],
            a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def _apply(m, pts):
    return [(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in pts]


def _style(el: ET.Element, inherited: dict) -> dict:
    st = dict(inherited)
    for k in ("fill", "stroke", "stroke-width", "opacity", "fill-opacity", "fill-rule", "display"):
        if el.get(k) is not None:
            st[k] = el.get(k)
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    return st


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _f(el, k, default=0.0) -> float:
    v = el.get(k)
    try:
        return float(re.sub(r"[a-z%]+$", "", v)) if v is not None else default
    except ValueError:
        return default


def svg_items(path: str | Path, tol: float = 0.5) -> tuple[list[dict], tuple[float, float, float, float], list[str]]:
    root = ET.parse(str(path)).getroot()
    vb = [float(x) for x in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if x] or [0, 0, _f(root, "width", 100),
                                                                                            _f(root, "height", 100)]
    defs: dict = {}
    for g in root.iter():
        if _local(g.tag) in ("linearGradient", "radialGradient") and g.get("id"):
            stop = next((s for s in g if _local(s.tag) == "stop"), None)
            if stop is not None:
                st = dict(_style(stop, {}))
                defs[g.get("id")] = color(stop.get("stop-color") or st.get("stop-color") or "#000000", {})
    items: list[dict] = []
    warnings: list[str] = []

    def walk(el, m, st):
        tag = _local(el.tag)
        if tag in ("defs", "clipPath", "mask", "symbol", "metadata", "title", "desc", "style", "linearGradient",
                   "radialGradient", "pattern"):
            return
        st = _style(el, st)
        if st.get("display") == "none":
            return
        m = _mul(m, _matrix(el.get("transform")))
        subs = None
        if tag == "path":
            subs = parse(el.get("d", ""), tol)
        elif tag == "rect":
            x, y, w, h = _f(el, "x"), _f(el, "y"), _f(el, "width"), _f(el, "height")
            rx = _f(el, "rx", _f(el, "ry"))
            if rx > 0:
                r = min(rx, w / 2, h / 2)
                subs = parse(f"M{x + r},{y} H{x + w - r} A{r},{r} 0 0 1 {x + w},{y + r} V{y + h - r} "
                             f"A{r},{r} 0 0 1 {x + w - r},{y + h} H{x + r} A{r},{r} 0 0 1 {x},{y + h - r} V{y + r} "
                             f"A{r},{r} 0 0 1 {x + r},{y} Z", tol)
            else:
                subs = [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
        elif tag in ("circle", "ellipse"):
            cx, cy = _f(el, "cx"), _f(el, "cy")
            rx = _f(el, "r") if tag == "circle" else _f(el, "rx")
            ry = _f(el, "r") if tag == "circle" else _f(el, "ry")
            subs = parse(f"M{cx - rx},{cy} A{rx},{ry} 0 1 0 {cx + rx},{cy} A{rx},{ry} 0 1 0 {cx - rx},{cy} Z", tol)
        elif tag == "line":
            subs = [([(_f(el, "x1"), _f(el, "y1")), (_f(el, "x2"), _f(el, "y2"))], False)]
        elif tag in ("polyline", "polygon"):
            n = [float(v) for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", el.get("points", ""))]
            subs = [(list(zip(n[0::2], n[1::2])), tag == "polygon")]
        elif tag == "text":
            warnings.append("an SVG <text> element was skipped: convert text to outlines (paths) before importing")
            return
        elif tag == "use":
            warnings.append("an SVG <use> element was skipped")
            return
        if subs is not None:
            fill = color(st.get("fill", "#000000"), defs)
            stroke = color(st.get("stroke"), defs)
            op = float(st.get("opacity", 1)) * float(st.get("fill-opacity", 1))
            scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0
            items.append({"subpaths": [(_apply(m, p), c) for p, c in subs], "fill": fill, "stroke": stroke,
                          "stroke_width": _f(el, "stroke-width", float(st.get("stroke-width", 1) or 1)) * scale
                          if stroke else 0.0, "opacity": max(0.0, min(1.0, op)),
                          "evenodd": st.get("fill-rule") == "evenodd"})
        for ch in el:
            walk(ch, m, st)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), {})
    if not items:
        raise PathError(f"{Path(path).name}: no shapes found")
    return items, tuple(vb[:4]), sorted(set(warnings))
