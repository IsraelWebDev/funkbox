"""Small, safe operations on video files with the bundled ffmpeg: look inside, find the cuts, trim, and burn in
subtitles whose text is exactly what you wrote.

    info("clip.mp4")                         -> Info(duration, width, height, fps, audio, codec)
    scenes("trailer.mp4")                     -> Scenes(shots=[(start, end), ...], sheet image), ready-made reel
                                                 sources {"video": ..., "trim": [a, b]} for each shot
    trim("clip.mp4", 3.2, 6.0)                -> a new file; frame-accurate (re-encoded) or fast (stream copy)
    subtitles("clip.mp4", cues=[...] | srt=)  -> a new file with captions drawn by funkbox's caption engine
                                                 (brand fonts, Hebrew right to left, a text audit)

Inputs are never modified and outputs never overwrite anything: a name that exists gets -2, -3 ...
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from funkbox.media import filter_script_args, require_ffmpeg

TIME_RE = re.compile(r"(\d+):(\d\d):(\d\d(?:[.,]\d+)?)")


class VideoError(ValueError):
    pass


@dataclass
class Info:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    audio: bool
    codec: str

    def text(self) -> str:
        return (
            f"{self.path.name}: {self.duration:.2f}s, {self.width}x{self.height} @ {self.fps:g} fps, "
            f"{self.codec}, {'with' if self.audio else 'no'} audio"
        )


def _src(path) -> Path:
    from funkbox.config import resolve_path

    return resolve_path(str(path))


def _run(args: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run([require_ffmpeg(), "-hide_banner", *args], capture_output=True)
    return r


def seconds(t: str | float | int) -> float:
    """12.5, "12.5", "1:02.5" or "00:01:02,500" -> seconds."""
    if isinstance(t, (int, float)):
        return float(t)
    s = str(t).strip().replace(",", ".")
    parts = s.split(":")
    try:
        v = 0.0
        for p in parts:
            v = v * 60 + float(p)
        return v
    except ValueError:
        raise VideoError(f"time {t!r}: use seconds (12.5) or [hh:]mm:ss(.ms)") from None


def info(path) -> Info:
    p = _src(path)
    err = _run(["-i", str(p)]).stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*" + TIME_RE.pattern, err)
    v = re.search(r"Stream #\S+.*?: Video: (\w+).*?(\d{2,5})x(\d{2,5})", err)
    if not m or not v:
        raise VideoError(f"{p.name}: not a video ffmpeg can read")
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    fps = re.search(r"(\d+(?:\.\d+)?) fps", err) or re.search(r"(\d+(?:\.\d+)?) tbr", err)
    return Info(
        p,
        dur,
        int(v.group(2)),
        int(v.group(3)),
        float(fps.group(1)) if fps else 0.0,
        bool(re.search(r"Stream #\S+.*?: Audio:", err)),
        v.group(1),
    )


def _out_path(src: Path, out_dir, name: str, suffix: str = ".mp4") -> Path:
    from funkbox.config import funk_out
    from funkbox.stills.render import NAME_RE, fence_out

    if not NAME_RE.match(name):
        raise VideoError(f"bad output name {name!r}: use 1-60 of letters, digits, _ and -")
    d = fence_out(out_dir or (funk_out() / "video"))
    d.mkdir(parents=True, exist_ok=True)
    p, n = d / f"{name}{suffix}", 2
    while p.exists():
        p, n = d / f"{name}-{n}{suffix}", n + 1
    return p


def _default_name(src: Path, tag: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", src.stem).strip("-")[:40] or "video"
    return f"{stem}-{tag}"


# --------------------------------------------------------------------------- scenes
@dataclass
class Scenes:
    path: Path
    shots: list[tuple[float, float]]
    sheet: Image.Image | None = None
    files: dict[str, Path] = field(default_factory=dict)

    def sources(self, rel: str | None = None) -> list[dict]:
        name = rel or self.path.name
        return [{"video": name, "trim": [round(a, 2), round(b, 2)]} for a, b in self.shots]

    def text(self, limit: int = 40) -> str:
        rows = [f"{len(self.shots)} shots in {self.path.name}"]
        rows += [f"  #{i}: {a:.2f}-{b:.2f} ({b - a:.2f}s)" for i, (a, b) in enumerate(self.shots[:limit])]
        if len(self.shots) > limit:
            rows.append(f"  ... {len(self.shots) - limit} more in the .scenes.json")
        return "\n".join(rows)


def scenes(path, threshold: float = 0.3, min_shot: float = 0.25, sheet: bool = True) -> Scenes:
    """Cut points where the picture changes (ffmpeg's scene score > ``threshold``, 0..1: lower finds more), merged
    so no shot is shorter than ``min_shot`` seconds. The sheet shows each shot's middle frame with its times."""
    if not 0.05 <= threshold <= 0.95:
        raise VideoError("threshold: 0.05..0.95 (lower finds more cuts)")
    inf = info(path)
    err = _run(["-i", str(inf.path), "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"])
    cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", err.stderr.decode("utf-8", "replace"))]
    bounds = [0.0]
    for c in cuts:
        if c - bounds[-1] >= min_shot and inf.duration - c >= min_shot:
            bounds.append(c)
    bounds.append(inf.duration)
    res = Scenes(inf.path, [(a, b) for a, b in zip(bounds, bounds[1:])])
    if sheet:
        res.sheet = _sheet(inf, res.shots)
    return res


def frame_at(path: Path, t: float, width: int = 256) -> Image.Image:
    r = _run(
        [
            "-ss",
            f"{max(0.0, t):.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
    )
    if r.returncode != 0 or not r.stdout:
        raise VideoError(f"{path.name}: no frame at {t:.2f}s")
    import io

    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def _sheet(inf: Info, shots, cols: int = 8, tw: int = 240) -> Image.Image:
    th = max(1, round(tw * inf.height / max(1, inf.width)))
    shots = shots[:96]
    rows = -(-len(shots) // cols)
    S = Image.new("RGB", (cols * tw, rows * (th + 18)), (20, 20, 24))
    d = ImageDraw.Draw(S)
    for i, (a, b) in enumerate(shots):
        r, c = divmod(i, cols)
        try:
            S.paste(frame_at(inf.path, (a + b) / 2, tw).resize((tw, th)), (c * tw, r * (th + 18)))
        except VideoError:
            pass
        d.text((c * tw + 3, r * (th + 18) + th + 3), f"#{i} {a:.1f}-{b:.1f}", fill=(255, 220, 0))
    return S


def write_scenes(res: Scenes, out_dir=None, name: str = "") -> dict[str, Path]:
    name = name or _default_name(res.path, "scenes")
    js = _out_path(res.path, out_dir, name, ".scenes.json")
    js.write_text(
        json.dumps({"video": str(res.path), "shots": [list(s) for s in res.shots], "sources": res.sources()}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    res.files = {"scenes": js}
    if res.sheet is not None:
        img = js.with_name(js.name.replace(".scenes.json", ".scenes.jpg"))
        res.sheet.save(img, quality=85)
        res.files["sheet"] = img
    return res.files


# --------------------------------------------------------------------------- trim
def trim(path, start, end=None, out_dir=None, name: str = "", accurate: bool = True, duration=None) -> Path:
    """Cut [start, end) (or ``duration`` seconds from start) into a new file. ``accurate`` re-encodes (exact frames,
    H.264 + AAC); ``accurate=False`` copies the streams (instant, but starts on the nearest earlier keyframe)."""
    inf = info(path)
    a = seconds(start)
    b = seconds(end) if end is not None else (a + seconds(duration) if duration is not None else inf.duration)
    if not 0 <= a < b:
        raise VideoError(f"trim: need 0 <= start < end (got {a:g}..{b:g})")
    if a >= inf.duration:
        raise VideoError(f"trim: start {a:g}s is past the end ({inf.duration:.2f}s)")
    b = min(b, inf.duration)
    out = _out_path(inf.path, out_dir, name or _default_name(inf.path, f"{a:g}-{b:g}s".replace(".", "_")))
    args = ["-ss", f"{a:.3f}", "-i", str(inf.path), "-t", f"{b - a:.3f}"]
    args += (
        ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
        if accurate
        else ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    )
    r = _run([*args, "-movflags", "+faststart", "-y", str(out)])
    if r.returncode != 0:
        out.unlink(missing_ok=True)
        raise VideoError("ffmpeg trim failed: " + r.stderr.decode("utf-8", "replace").strip()[-400:])
    return out


# --------------------------------------------------------------------------- subtitles
@dataclass
class Cue:
    start: float
    end: float
    text: str


def parse_srt(text: str) -> list[Cue]:
    """SubRip or WebVTT text -> cues (tags like <i> are kept as written: funkbox never rewrites text)."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        k = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if k is None:
            continue
        a, b = (x.strip().split(" ")[0] for x in lines[k].split("-->"))
        body = "\n".join(lines[k + 1 :]).strip()
        if body:
            cues.append(Cue(seconds(a), seconds(b), body))
    return cues


def _cues(cues=None, srt=None) -> list[Cue]:
    if srt:
        p = _src(srt)
        out = parse_srt(p.read_text(encoding="utf-8-sig"))
    else:
        out = []
        for i, c in enumerate(cues or []):
            if not isinstance(c, dict) or not isinstance(c.get("text"), str) or "start" not in c or "end" not in c:
                raise VideoError(f"cues[{i}]: {{start, end, text}}")
            out.append(Cue(seconds(c["start"]), seconds(c["end"]), c["text"]))
    if not out:
        raise VideoError("subtitles: give cues [{start, end, text}] or an .srt/.vtt file")
    for i, c in enumerate(out):
        if not 0 <= c.start < c.end:
            raise VideoError(f"cue {i + 1} ({c.text[:20]!r}): start must be before end")
    return out


@dataclass
class Subtitled:
    path: Path
    audit: Path
    cues: int


def subtitles(
    path,
    cues=None,
    srt=None,
    style: str = "lower-third",
    anchor: str = "bottom",
    out_dir=None,
    name: str = "",
    brand: dict | None = None,
) -> Subtitled:
    """Burn cues into a copy of the video with funkbox's caption styles (red-box, bold-pop, lower-third).
    Each cue is laid out like a reel caption (wrapped on spaces, never shrunk below the minimum, Hebrew right to
    left) and a text audit records every string drawn."""
    import tempfile
    from funkbox.reel import captions
    from funkbox.reel.schema import Brand

    inf = info(path)
    cs = _cues(cues, srt)
    if style not in captions.list_styles():
        raise VideoError(f"style {style!r}: one of {', '.join(captions.list_styles())}")
    if anchor not in ("top", "center", "bottom"):
        raise VideoError("anchor: top, center or bottom")
    b = Brand(**{k: v for k, v in (brand or {}).items() if k in Brand.__dataclass_fields__})
    fonts = captions.fonts_for(b, None)
    st = captions.load_style(style)
    W, H = inf.width, inf.height
    safe = "reels" if H > W else "landscape"
    out = _out_path(inf.path, out_dir, name or _default_name(inf.path, "subtitled"))
    audit = []
    with tempfile.TemporaryDirectory() as td:
        inputs, chain, last = [], [], "0:v"
        for i, c in enumerate(cs):
            lay = captions.layout_block(f"cue{i + 1}", c.text, st, fonts, b, W, H, anchor=anchor, safe_area=safe)
            spr = captions.render_caption(lay, "none", 10.0)
            if spr is None:
                continue
            png = Path(td) / f"c{i}.png"
            Image.fromarray(spr.rgba, "RGBA").save(png)
            inputs += ["-i", str(png)]
            k = len(inputs) // 2
            tag = f"v{k}"
            chain.append(f"[{last}][{k}:v]overlay={spr.x}:{spr.y}:enable='between(t,{c.start:.3f},{c.end:.3f})'[{tag}]")
            last = tag
            rebuilt = lay.reconstruct(spr.drawn)
            audit.append(
                {
                    "cue": i + 1,
                    "start": c.start,
                    "end": c.end,
                    "text": c.text,
                    "drawn_lines": spr.drawn,
                    "text_exact": rebuilt == c.text,
                }
            )
        script = Path(td) / "graph.txt"
        script.write_text(";".join(chain) if chain else "[0:v]null[v0]", encoding="utf-8")
        args = [
            "-i",
            str(inf.path),
            *inputs,
            *filter_script_args(script),
            "-map",
            f"[{last if chain else 'v0'}]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(out),
        ]
        r = _run(args)
    if r.returncode != 0:
        out.unlink(missing_ok=True)
        raise VideoError("ffmpeg subtitles failed: " + r.stderr.decode("utf-8", "replace").strip()[-500:])
    ap = out.with_suffix(".text_audit.json")
    ap.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not all(a["text_exact"] for a in audit):
        raise VideoError(f"subtitles: a cue was not drawn exactly; see {ap}")
    return Subtitled(out, ap, len(audit))
