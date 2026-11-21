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
                                                 flash or a cut, audio crossfaded, aimed at an optional size target

Inputs are never modified and outputs never overwrite anything: a name that exists gets -2, -3 ...
"""

from __future__ import annotations

import json
import re
import shutil
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
TRANSITIONS = ("flash", "cut")
GRADES = ("funky", "none")
ASPECTS = {"9:16": (720, 1280), "16:9": (1280, 720)}
FLASH_S = 0.25  # the white flash, and the audio crossfade, between two cuts
FUNKY = "eq=saturation=1.45:contrast=1.12:brightness=0.02,colorbalance=rm=0.08:bm=0.06:gh=-0.03,unsharp=5:5:0.6,vignette=PI/5"
AUDIO_KBPS = 96


@dataclass
class Edited:
    path: Path
    cuts: int
    duration: float

    def text(self) -> str:
        mb = self.path.stat().st_size / (1024 * 1024)
        return f"{self.cuts} cuts, {self.duration:.2f}s, {mb:.1f} MB"


def edit(
    cuts,
    transition: str = "flash",
    grade: str = "funky",
    aspect: str = "9:16",
    target_mb: float | None = None,
    out_dir=None,
    name: str = "",
) -> Edited:
    """Render a list of cuts ``[{"path", "start", "end"}, ...]`` into one file in FUNK_OUT/video/. Each cut is padded
    (never cropped) to the aspect at 30 fps; ``grade="funky"`` boosts colour and adds a vignette; ``transition="flash"``
    joins cuts with a white flash (``cut`` joins them hard). ``target_mb`` sets the video bitrate to land near that size;
    without it the picture is CRF 22."""
    import tempfile

    if transition not in TRANSITIONS:
        raise VideoError(f"transition must be one of {', '.join(TRANSITIONS)}")
    if grade not in GRADES:
        raise VideoError(f"grade must be one of {', '.join(GRADES)}")
    if aspect not in ASPECTS:
        raise VideoError(f"aspect must be one of {', '.join(ASPECTS)}")
    if not cuts:
        raise VideoError("edit needs at least one cut")
    W, H = ASPECTS[aspect]
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
    dur = [b - a for _, a, b in segs]
    flash = transition == "flash" and len(segs) > 1
    if flash and min(dur) < 2 * FLASH_S:
        raise VideoError(f"a cut is too short for a flash ({min(dur):.2f}s; each needs at least {2 * FLASH_S}s)")
    n = len(segs)
    grade_f = f",{FUNKY}" if grade == "funky" else ""
    parts, inputs = [], []
    for i, (src, a, b) in enumerate(segs):
        inputs += ["-i", str(src.path)]
        parts.append(
            f"[{i}:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps=30{grade_f},format=yuv420p[v{i}]"
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
        venc = ["-crf", "22"]
    out = _out_path(segs[0][0].path, out_dir, name or "edit")
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "graph.txt"
        script.write_text(";".join(parts), encoding="utf-8")
        args = [
            *inputs,
            *filter_script_args(script),
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", *venc, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k",
            "-movflags", "+faststart", "-y", str(out),
        ]  # fmt: skip
        r = _run(args)
    if r.returncode != 0:
        out.unlink(missing_ok=True)
        raise VideoError("ffmpeg edit failed: " + r.stderr.decode("utf-8", "replace").strip()[-400:])
    return Edited(out, n, info(out).duration)
