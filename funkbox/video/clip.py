"""Small, safe operations on video files with the bundled ffmpeg: look inside, find the cuts, trim, and burn in
subtitles whose text is exactly what you wrote.

    info("clip.mp4")                         -> Info(duration, width, height, fps, audio, codec)
    scenes("trailer.mp4")                     -> Scenes(shots=[(start, end), ...], sheet image), ready-made reel
                                                 sources {"video": ..., "trim": [a, b]} for each shot
    trim("clip.mp4", 3.2, 6.0)                -> a new file; frame-accurate (re-encoded) or fast (stream copy)
    subtitles("clip.mp4", cues=[...] | srt=)  -> a new file with captions drawn by funkbox's caption engine
                                                 (brand fonts, Hebrew right to left, a text audit)
    import_clips("camera_folder")             -> the video files of a folder copied into FUNK_INPUTS, never overwriting
    edit([{"path", "start", "end"}, ...])     -> one render: each cut padded to 9:16 or 16:9, graded, joined with a white
                                                 flash or a cut, audio crossfaded, shaky sources steadied, aimed at an optional size target
    highlights(["a.mov", "b.mov"], target_s=(30, 60)) -> proposed cuts ranked by motion, dropping repeats, with a sheet
                                                 to approve before edit()

Inputs are never modified and outputs never overwrite anything: a name that exists gets -2, -3 ...
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
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
    return subprocess.run([require_ffmpeg(), "-hide_banner", *args], capture_output=True)


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
    if end is not None:
        b = seconds(end)
    elif duration is not None:
        b = a + seconds(duration)
    else:
        b = inf.duration
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


# --------------------------------------------------------------------------- contact sheet
MAX_SHEET_FRAMES = 240


@dataclass
class ContactSheet:
    path: Path
    times: list[float]
    image: Image.Image

    def text(self) -> str:
        return (
            f"{len(self.times)} frames every {self.times[1] - self.times[0]:g}s"
            if len(self.times) > 1
            else (f"{len(self.times)} frame")
        )


def contact_sheet(
    path, every_s: float = 2.0, cols: int = 6, tw: int = 240, name: str = "", out_dir=None
) -> ContactSheet:
    """A grid of frames every ``every_s`` seconds, each labelled with its time, saved as <name>-contact.jpg. Use it to
    find moments in a clip that cuts miss (single takes, home video)."""
    if every_s <= 0:
        raise VideoError("every_s must be more than 0 seconds")
    if not 1 <= cols <= 12:
        raise VideoError("cols: 1..12")
    inf = info(path)
    times = []
    t = 0.0
    while t < inf.duration:
        times.append(round(t, 3))
        t += every_s
    if len(times) > MAX_SHEET_FRAMES:
        raise VideoError(f"{len(times)} frames is too many: use a longer every_s (at most {MAX_SHEET_FRAMES})")
    th = max(1, round(tw * inf.height / max(1, inf.width)))
    rows = -(-len(times) // cols)
    S = Image.new("RGB", (cols * tw, rows * (th + 18)), (20, 20, 24))
    d = ImageDraw.Draw(S)
    for i, t in enumerate(times):
        r, c = divmod(i, cols)
        try:
            S.paste(frame_at(inf.path, t, tw).resize((tw, th)), (c * tw, r * (th + 18)))
        except VideoError:
            pass
        d.text((c * tw + 3, r * (th + 18) + th + 3), f"{t:.1f}s", fill=(255, 220, 0))
    p = _out_path(inf.path, out_dir, name or _default_name(inf.path, "contact"), ".jpg")
    S.save(p, quality=85)
    return ContactSheet(p, times, S)


# --------------------------------------------------------------------------- import
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".3gp")


@dataclass
class Imported:
    dest: Path
    copied: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)  # already in dest under the same name

    def text(self) -> str:
        lines = [f"{len(self.copied)} copied, {len(self.skipped)} already there -> {self.dest}"]
        lines += [f"  + {p.name}" for p in self.copied]
        lines += [f"  = {p.name} (kept, not overwritten)" for p in self.skipped]
        return "\n".join(lines)


def import_clips(folder, dest=None) -> Imported:
    """Copy the video files of one folder (not its sub-folders) into ``dest``, default FUNK_INPUTS/<folder name>.
    Existing files are never overwritten. The source must sit inside an allowed folder (see funkbox.config)."""
    from funkbox.config import funk_inputs

    src = _src(folder)
    if not src.is_dir():
        raise VideoError(f"not a folder: {folder}")
    clips = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    if not clips:
        raise VideoError(f"no video files in {folder} (looked for {', '.join(VIDEO_EXTS)})")
    target = Path(dest) if dest else funk_inputs() / src.name
    res = Imported(dest=target)
    if target.resolve() == src.resolve():
        res.skipped = clips
        return res
    target.mkdir(parents=True, exist_ok=True)
    for c in clips:
        to = target / c.name
        if to.exists():
            res.skipped.append(to)
            continue
        shutil.copy2(c, to)
        res.copied.append(to)
    return res


# --------------------------------------------------------------------------- edit
# --------------------------------------------------------------------------- stabilization
STABILIZE = ("auto", "off")
STAB_MIN_SHAKE = 0.3  # jitter, percent of the frame width per frame, from which a source is stabilized
STAB_SMOOTH_S = 1.0  # motion slower than this (a pan, a walk) is intent and kept; faster motion is shake
STAB_W, STAB_H = 320, 180  # analysis size for the shake check
STAB_DETECT = "vidstabdetect=shakiness=5:accuracy=15:result=transforms.trf"
STAB_TRANSFORM = (
    "vidstabtransform=input=transforms.trf:smoothing=15:zoom=0:optzoom=1:interpol=bicubic,"
    "unsharp=5:5:0.8:3:3:0.4"
)  # the unsharp pass restores the edge the transform softens
_FILTERS: str | None = None


def _has_filter(name: str) -> bool:
    global _FILTERS
    if _FILTERS is None:
        _FILTERS = _run(["-filters"]).stdout.decode("utf-8", "replace")
    return re.search(rf"^\s*\S+\s+{name}\s+\S+->", _FILTERS, re.M) is not None


def _stab_cache() -> Path:
    from funkbox.config import funk_out
    from funkbox.stills.render import fence_out

    d = fence_out(funk_out() / "video" / "stab-cache")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stab_key(path: Path) -> str:
    st = path.stat()
    return hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]


def _run_in(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    # run in a scratch folder so the detect file is a bare name: no drive colons to escape in the filter graph
    return subprocess.run([require_ffmpeg(), "-hide_banner", *args], capture_output=True, cwd=cwd)


def _shift(a, b) -> tuple[float, float]:
    """Whole-pixel translation between two frames, by phase correlation."""
    import numpy as np

    h, w = a.shape
    r = np.fft.ifft2(np.fft.fft2(b - b.mean()) * np.conj(np.fft.fft2(a - a.mean()))).real
    r = r / (np.abs(r).max() + 1e-9)
    y, x = np.unravel_index(int(np.argmax(r)), r.shape)

    def sub(m, p, q):  # parabola through the peak and its two neighbours: a fraction of a pixel
        den = m - 2 * p + q
        return 0.5 * (m - q) / den if abs(den) > 1e-12 else 0.0

    fx = sub(r[y, (x - 1) % w], r[y, x], r[y, (x + 1) % w])
    fy = sub(r[(y - 1) % h, x], r[y, x], r[(y + 1) % h, x])
    xs, ys = x + fx, y + fy
    return (xs - w if xs > w / 2 else xs), (ys - h if ys > h / 2 else ys)


def _shake(path: Path, fps: float) -> float:
    """Camera shake of a source: how far each frame's motion strays from the smooth motion around it, as percent of the
    frame width. A pan or a walk is slow and steady, so it is kept; the quick back-and-forth of a shaky hand counts. A
    still picture measures 0. Frames are streamed, so a long clip does not fill the memory."""
    import numpy as np

    w, h = STAB_W, STAB_H
    proc = subprocess.Popen(
        [require_ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
         "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    dx, dy, prev = [], [], None
    try:
        while len(buf := proc.stdout.read(w * h)) == w * h:
            cur = np.frombuffer(buf, np.uint8).reshape(h, w).astype(np.float32)
            if prev is not None:
                x, y = _shift(prev, cur)
                dx.append(x)
                dy.append(y)
            prev = cur
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode != 0 or len(dx) < 3:
        raise VideoError(f"{path.name}: too short or unreadable to check for shake")
    k = max(3, round(fps * STAB_SMOOTH_S))
    kern = np.ones(k)
    norm = np.convolve(np.ones(len(dx)), kern, mode="same")  # edges average over what is there, not zeros
    rx = np.array(dx, float) - np.convolve(dx, kern, mode="same") / norm
    ry = np.array(dy, float) - np.convolve(dy, kern, mode="same") / norm
    return 100 * float(np.sqrt(np.mean(rx**2 + ry**2))) / w


def _stabilize_source(path: Path, fps: float) -> tuple[Path, float]:
    """The file to read for a source, and its shake. The shake is checked once per file and cached. A shaky source
    (at or above ``STAB_MIN_SHAKE``) is transformed once with vidstab and the copy cached; a steady one is read as it
    is. Timing is kept, so the cut points and flashes do not move."""
    cache = _stab_cache()
    key = _stab_key(path)
    done, meta = cache / f"{key}.mp4", cache / f"{key}.json"
    if meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        return (done if m["stabilized"] else path), m["shake"]
    shake = _shake(path, fps)
    stab = shake >= STAB_MIN_SHAKE
    if stab:
        with tempfile.TemporaryDirectory() as td:
            r = _run_in(["-i", str(path), "-vf", STAB_DETECT, "-f", "null", "-"], td)
            if r.returncode == 0:
                r = _run_in(
                    ["-i", str(path), "-vf", STAB_TRANSFORM, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-y", str(done)],
                    td,
                )  # fmt: skip
            if r.returncode != 0:
                done.unlink(missing_ok=True)
                raise VideoError("stabilize failed: " + r.stderr.decode("utf-8", "replace").strip()[-300:])
    meta.write_text(json.dumps({"shake": round(shake, 3), "stabilized": stab}), encoding="utf-8")
    return (done if stab else path), shake


TRANSITIONS = ("flash", "cut")
GRADES = ("funky", "warm", "none")
FRAMES = ("pad", "crop", "blurred-bg")
ASPECTS = {"9:16": (720, 1280), "16:9": (1280, 720)}
FLASH_S = 0.25  # the white flash, and the audio crossfade, between two cuts
FUNKY = "eq=saturation=1.45:contrast=1.12:brightness=0.02,colorbalance=rm=0.08:bm=0.06:gh=-0.03,unsharp=5:5:0.6,vignette=PI/5"
WARM = "colorchannelmixer=rr=1.08:bb=0.92,eq=saturation=1.08"  # colorbalance midtones do not move a yuv frame reliably
AUDIO_KBPS = 96


@dataclass
class Edited:
    path: Path
    cuts: int
    duration: float
    preview: bool = False
    stabilized: int = 0  # sources that were shaky and got the stabilize pass
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        mb = self.path.stat().st_size / (1024 * 1024)
        parts = [f"{'preview, ' if self.preview else ''}{self.cuts} cuts, {self.duration:.2f}s, {mb:.1f} MB"]
        if self.stabilized:
            parts.append(f"stabilized {self.stabilized} shaky source{'s' if self.stabilized != 1 else ''}")
        parts += self.notes
        return ", ".join(parts)


def edit(
    cuts,
    transition: str = "flash",
    grade: str = "funky",
    aspect: str = "9:16",
    frame: str = "pad",
    target_mb: float | None = None,
    out_dir=None,
    name: str = "",
    preview: bool = False,
    stabilize: str = "auto",
) -> Edited:
    """Render a list of cuts ``[{"path", "start", "end"}, ...]`` into one file in FUNK_OUT/video/. Each cut is padded
    (never cropped) to the aspect at 30 fps; ``grade="funky"`` boosts colour and adds a vignette; ``transition="flash"``
    joins cuts with a white flash (``cut`` joins them hard). ``target_mb`` sets the video bitrate to land near that size;
    without it the picture is CRF 22. ``stabilize="auto"`` checks each source for camera shake and steadies only the
    shaky ones (cached per file); ``"off"`` uses the picture as shot."""
    if stabilize not in STABILIZE:
        raise VideoError(f"stabilize must be one of {', '.join(STABILIZE)}")
    if transition not in TRANSITIONS:
        raise VideoError(f"transition must be one of {', '.join(TRANSITIONS)}")
    if grade not in GRADES:
        raise VideoError(f"grade must be one of {', '.join(GRADES)}")
    if aspect not in ASPECTS:
        raise VideoError(f"aspect must be one of {', '.join(ASPECTS)}")
    if frame not in FRAMES:
        raise VideoError(f"frame must be one of {', '.join(FRAMES)}")
    if not cuts:
        raise VideoError("edit needs at least one cut")
    W, H = ASPECTS[aspect]
    if preview:  # half size, the same timing: a bad cut shows in seconds
        W, H = W // 2, H // 2
    segs = []
    for n, c in enumerate(cuts, 1):
        if not all(k in c for k in ("path", "start", "end")):
            raise VideoError(f"cut {n}: each cut needs path, start and end")
        src = info(c["path"])
        a, b = seconds(c["start"]), seconds(c["end"])
        if not 0 <= a < b:
            raise VideoError(f"cut {n}: need start < end (got {a:g}..{b:g})")
        if a >= src.duration:
            raise VideoError(f"cut {n}: start {a:g}s is past the end of the video ({src.duration:.2f}s)")
        segs.append((src, a, min(b, src.duration)))
    read: dict[Path, Path] = {}  # source -> the file to read: its stabilized copy when it was shaky
    stabilized, notes = 0, []
    if stabilize == "auto" and not _has_filter("vidstabdetect"):
        notes.append("stabilize skipped: this ffmpeg has no vidstab")
    elif stabilize == "auto":
        for src, _, _ in segs:
            if src.path in read:
                continue
            f, _ = _stabilize_source(src.path, src.fps or 30.0)
            read[src.path] = f
            stabilized += f != src.path
    dur = [b - a for _, a, b in segs]
    flash = transition == "flash" and len(segs) > 1
    if flash and min(dur) < 2 * FLASH_S:
        raise VideoError(f"a cut is too short for a flash ({min(dur):.2f}s; each needs at least {2 * FLASH_S}s)")
    n = len(segs)
    grade_f = {"funky": f",{FUNKY}", "warm": f",{WARM}", "none": ""}[grade]
    parts, inputs = [], []
    for i, (src, a, b) in enumerate(segs):
        inputs += ["-i", str(read.get(src.path, src.path))]
        # fps before trim: a camera file with timestamp gaps trims to the wrong length otherwise, and the flash
        # joins (which assume each cut is its nominal length) then end the picture early
        head = f"[{i}:v]fps=30,trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS"
        if frame == "blurred-bg":  # the picture fitted on a blurred copy of itself, which fills the bars
            parts.append(
                f"{head},split=2[bs{i}][fs{i}];"
                f"[bs{i}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2[bb{i}];"
                f"[fs{i}]scale={W}:{H}:force_original_aspect_ratio=decrease[ff{i}];"
                f"[bb{i}][ff{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30{grade_f},format=yuv420p[v{i}]"
            )
        elif frame == "crop":  # fills the frame, the edges go
            parts.append(
                f"{head},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"setsar=1,fps=30{grade_f},format=yuv420p[v{i}]"
            )
        else:  # pad: the whole picture, black bars where it does not fill the frame
            parts.append(
                f"{head},scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30{grade_f},format=yuv420p[v{i}]"
            )
        if src.audio:
            parts.append(
                f"[{i}:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            )
        else:  # a silent source still gets a silent track, so the joins line up
            parts.append(f"aevalsrc=0:d={dur[i]:.3f}:s=48000:c=stereo[a{i}]")
    if flash:
        cur, acc = "v0", dur[0]
        acur = "a0"
        for k in range(1, n):
            last = k == n - 1
            vo, ao = ("vout" if last else f"vx{k}"), ("aout" if last else f"ax{k}")
            parts.append(f"[{cur}][v{k}]xfade=transition=fadewhite:duration={FLASH_S}:offset={acc - FLASH_S:.3f}[{vo}]")
            parts.append(f"[{acur}][a{k}]acrossfade=d={FLASH_S}:c1=tri:c2=tri[{ao}]")
            cur, acur = vo, ao
            acc += dur[k] - FLASH_S
        total = acc
    else:
        vin = "".join(f"[v{i}]" for i in range(n))
        ain = "".join(f"[a{i}]" for i in range(n))
        parts.append(f"{vin}concat=n={n}:v=1:a=0[vout]")
        parts.append(f"{ain}concat=n={n}:v=0:a=1[aout]")
        total = sum(dur)
    if target_mb:
        kbps = target_mb * 8 * 1024 * 1024 * 0.92 / total / 1000 - AUDIO_KBPS
        if kbps < 150:
            raise VideoError(f"target too small: {target_mb} MB for {total:.1f}s needs about 150 kbps or more")
        venc = ["-b:v", f"{int(kbps)}k", "-maxrate", f"{int(kbps * 1.2)}k", "-bufsize", f"{int(kbps * 2)}k"]
    else:
        venc = ["-crf", "30" if preview else "22"]
    out = _out_path(segs[0][0].path, out_dir, name or ("preview" if preview else "edit"))
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "graph.txt"
        script.write_text(";".join(parts), encoding="utf-8")
        args = [
            *inputs,
            *filter_script_args(script),
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast" if preview else "medium", *venc, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
            "-movflags", "+faststart", "-y", str(out),
        ]  # fmt: skip
        r = _run(args)
    if r.returncode != 0:
        out.unlink(missing_ok=True)
        raise VideoError("ffmpeg edit failed: " + r.stderr.decode("utf-8", "replace").strip()[-400:])
    return Edited(out, n, info(out).duration, preview, stabilized, notes)


# --------------------------------------------------------------------------- highlights
HL_FPS = 2.0  # frames per second sampled for the analysis
HL_W, HL_H = 64, 36  # analysis size, small on purpose
HL_MIN_MOTION = 0.005  # mean change per pixel per step; below this a window is still
HL_REPEAT_SIM = 0.9  # histogram overlap above which a window repeats one already chosen


@dataclass
class Highlights:
    cuts: list[dict]  # [{"path", "start", "end"}], ready for edit()
    total: float
    short: bool  # fewer seconds than the target's minimum were found
    sheet: Image.Image
    path: Path  # <name>.jpg, one labelled frame per cut
    json_path: Path  # <name>.json, the cut list

    def text(self) -> str:
        head = f"{len(self.cuts)} moments, {self.total:.0f}s"
        if self.short:
            head += " (short of the target: not enough different motion in these clips)"
        return head


def _frames(path: Path):
    import numpy as np

    r = _run(["-i", str(path), "-vf", f"fps={HL_FPS},scale={HL_W}:{HL_H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    if r.returncode != 0:
        raise VideoError(f"could not read frames of {path.name}")
    n = len(r.stdout) // (HL_W * HL_H * 3)
    return np.frombuffer(r.stdout, np.uint8)[: n * HL_W * HL_H * 3].reshape(n, HL_H, HL_W, 3)


def _hist(frames):
    """Colour histogram of a window: 4 levels per channel, averaged over its frames, sums to 1."""
    import numpy as np

    q = (frames // 64).astype(np.int32)
    idx = q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]
    h = np.stack([np.bincount(f.ravel(), minlength=64) for f in idx]).mean(axis=0)
    return h / h.sum()


def highlights(paths, target_s=(30.0, 60.0), seg_s: float = 3.0, out_dir=None, name: str = "") -> Highlights:
    """Propose moments from one or more videos: windows of ``seg_s`` seconds ranked by motion, each new one scored
    by how different its colours are from the moments already chosen. Repeats are dropped and the reel is filled
    up to the top of ``target_s``. Nothing is cut yet: approve the sheet, then pass ``cuts`` to edit()."""
    import numpy as np

    lo, hi = (float(x) for x in target_s) if len(target_s) == 2 else (0.0, 0.0)
    if not 0 < lo < hi:
        raise VideoError("target_s needs two seconds, low then high (e.g. [30, 60])")
    if not 1 <= seg_s <= hi:
        raise VideoError(f"seg_s must be between 1 and the target's high end ({hi:g}s)")
    infos = [info(p) for p in paths]
    win = max(2, round(seg_s * HL_FPS))
    cands = []
    for si, inf in enumerate(infos):
        F = _frames(inf.path)
        for s0 in range(0, len(F) - win + 1, max(1, round(HL_FPS))):  # one window per second
            seg = F[s0 : s0 + win]
            motion = float(np.abs(np.diff(seg.astype(np.int16), axis=0)).mean()) / 255
            if motion < HL_MIN_MOTION:
                continue
            cands.append(
                {"src": si, "start": s0 / HL_FPS, "end": (s0 + win) / HL_FPS, "motion": motion, "hist": _hist(seg)}
            )
    if not cands:
        raise VideoError("nothing moving in these videos: no moments to highlight")
    chosen, total = [], 0.0
    while total + seg_s <= hi + 1e-9:
        best, best_score = None, 0.0
        for c in cands:
            if any(s["src"] == c["src"] and c["start"] < s["end"] and s["start"] < c["end"] for s in chosen):
                continue  # overlaps a moment already chosen from the same video
            sim = max((float(np.minimum(c["hist"], s["hist"]).sum()) for s in chosen), default=0.0)
            if sim >= HL_REPEAT_SIM:
                continue
            score = c["motion"] * (1.0 - sim)
            if score > best_score:
                best, best_score = c, score
        if best is None:
            break
        chosen.append(best)
        total += seg_s
    chosen.sort(key=lambda c: (c["src"], c["start"]))
    cuts = [
        {"path": str(infos[c["src"]].path), "start": round(c["start"], 2), "end": round(c["end"], 2)} for c in chosen
    ]
    tiles = [frame_at(c["path"], (c["start"] + c["end"]) / 2, 240) for c in cuts]
    cols = min(5, len(tiles))
    th = max(t.height for t in tiles)
    S = Image.new("RGB", (cols * 240, -(-len(tiles) // cols) * (th + 18)), (20, 20, 20))
    d = ImageDraw.Draw(S)
    for i, (t, c, ch) in enumerate(zip(tiles, cuts, chosen)):
        x, y = (i % cols) * 240, (i // cols) * (th + 18)
        S.paste(t.resize((240, round(240 * t.height / t.width))), (x, y))
        d.text(
            (x + 3, y + th + 3),
            f"{i + 1} {Path(c['path']).name} {c['start']:.1f}s m{ch['motion']:.3f}",
            fill=(255, 220, 0),
        )
    stem = name or "highlights"
    jpg = _out_path(infos[0].path, out_dir, stem, ".jpg")
    S.save(jpg, quality=85)
    jp = jpg.with_suffix(".json")
    jp.write_text(json.dumps(cuts, indent=1), encoding="utf-8")
    return Highlights(cuts, sum(c["end"] - c["start"] for c in cuts), total < lo, S, jpg, jp)
