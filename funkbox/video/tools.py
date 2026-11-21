"""MCP tools and CLI of the ``video`` area: look inside a video, find its cuts, trim it, burn in subtitles."""

from __future__ import annotations

from pathlib import Path


from funkbox.mcp_util import png as _png, safe
from funkbox.tool_options import CaptionStyle, SubtitleAnchor


@safe
def video_info(path: str) -> str:
    """Duration, size, frame rate, codec and whether a video file has audio."""
    from funkbox.video import clip

    return clip.info(path).text()


@safe
def video_scenes(path: str, threshold: float = 0.3, min_shot: float = 0.25, name: str = "") -> list:
    """Find the cuts in a video (trailers, gameplay, screen recordings) and show one frame per shot with its times.
    ``threshold`` 0.05..0.95: lower finds more cuts. Writes <out>/video/<name>.scenes.json, which lists every shot
    as a ready reel source {"video": ..., "trim": [start, end]}, and <name>.scenes.jpg. Pick shots from the sheet,
    then use their trims in a reel."""
    from funkbox.video import clip

    res = clip.scenes(path, threshold, min_shot)
    files = clip.write_scenes(res, None, name)
    out = [res.text() + "\n" + "\n".join(f"{k}: {v}" for k, v in files.items())]
    if res.sheet is not None:
        out.append(_png(res.sheet, 1600))
    return out


@safe
def video_trim(
    path: str,
    start: float | str,
    end: float | str | None = None,
    duration: float | str | None = None,
    name: str = "",
    accurate: bool = True,
) -> str:
    """Cut a piece of a video into a new file in <out>/video/ (the original is untouched, nothing is overwritten).
    Times are seconds or [hh:]mm:ss. ``accurate`` (default) re-encodes for exact frames; false copies the
    streams instantly from the nearest earlier keyframe."""
    from funkbox.video import clip

    p = clip.trim(path, start, end, None, name, accurate, duration)
    return clip.info(p).text() + f"\nfile: {p}"


@safe
def video_subtitles(
    path: str,
    cues: list | None = None,
    srt: str = "",
    style: CaptionStyle = "lower-third",
    anchor: SubtitleAnchor = "bottom",
    name: str = "",
    brand: dict | None = None,
) -> list:
    """Burn subtitles into a copy of a video. ``cues`` = [{"start": 1.2, "end": 3.0, "text": "..."}] or ``srt`` =
    a .srt/.vtt file. Drawn by funkbox's caption engine in a caption style (lower-third, red-box, bold-pop) with the
    brand's fonts and colours (brand = {primary, secondary, text, font_caption, font_display}); Hebrew reads right
    to left; text is never rewritten and a .text_audit.json proves each cue was drawn exactly."""
    from funkbox.video import clip

    res = clip.subtitles(path, cues, srt or None, style, anchor, None, name, brand)
    inf = clip.info(res.path)
    frame = clip.frame_at(res.path, min(inf.duration - 0.05, _first_cue_mid(cues, srt)), 720)
    return [f"{res.cues} cues drawn exactly\nfile: {res.path}\naudit: {res.audit}", _png(frame, 720)]


def _first_cue_mid(cues, srt) -> float:
    from funkbox.video import clip

    c = clip._cues(cues, srt or None)[0]
    return (c.start + c.end) / 2


@safe
def video_contact_sheet(path: str, every_s: float = 2.0, cols: int = 6, name: str = "") -> list:
    """A grid of frames every ``every_s`` seconds of a video, each labelled with its time, to find moments the cuts
    miss (single takes, home video). Writes <out>/video/<name>-contact.jpg and shows it."""
    from funkbox.video import clip

    res = clip.contact_sheet(path, every_s, cols, 240, name)
    return [f"{res.text()}\nfile: {res.path}", _png(res.image, 1600)]


@safe
def video_edit(
    cuts: list,
    transition: str = "flash",
    grade: str = "funky",
    aspect: str = "9:16",
    frame: str = "pad",
    target_mb: float | None = None,
    name: str = "",
) -> list:
    """Render a cut list [{"path": ..., "start": ..., "end": ...}, ...] into one video in <out>/video/, for example a
    highlight reel. ``aspect``: 9:16 or 16:9, at 30 fps. ``frame``: pad (the whole picture, bars where it does not
    fill), crop (fills the frame, the edges go) or blurred-bg (bars filled with a blurred copy). ``grade``: funky (more
    colour, a vignette), warm, or none. ``transition``: flash (a white flash between cuts, audio crossfaded) or cut.
    ``target_mb`` aims the bitrate at a file size. Take the trims from video_scenes or video_contact_sheet. Never
    overwrites."""
    from funkbox.video import clip

    res = clip.edit(
        cuts, transition=transition, grade=grade, aspect=aspect, frame=frame, target_mb=target_mb, name=name
    )
    frame = clip.frame_at(res.path, res.duration / 2, 720)
    return [f"{res.text()}\nfile: {res.path}", _png(frame, 720)]


@safe
def video_highlights(paths: list, target_s: list | None = None, seg_s: float = 3.0, name: str = "") -> list:
    """Propose the best moments of one or more videos (home video, gameplay, trailers): windows of ``seg_s`` seconds
    ranked by motion, each new one scored by how different it looks from the moments already chosen, so repeats are
    dropped. Fills up to the top of ``target_s`` (default [30, 60] seconds). Writes <out>/video/<name>.jpg, a labelled
    sheet to approve, and <name>.json, the cut list. Nothing is cut yet: after approval pass the cuts to video_edit."""
    import json

    from funkbox.video import clip

    res = clip.highlights(paths, tuple(target_s or [30, 60]), seg_s, None, name)
    cuts = json.dumps(res.cuts)
    text = f"{res.text()}\nsheet: {res.path}\ncut list: {res.json_path}\ncuts: {cuts}"
    return [text, _png(res.sheet, 1600)]


@safe
def video_import(folder: str) -> str:
    """Copy the video files of a folder (not its sub-folders) into the inputs folder, where the video tools and reels
    read them. Never overwrites: a file already there is kept and listed. The folder must be inside an allowed
    folder (out, inputs, projects or the funkbox folder); copy anything else in first."""
    from funkbox.video import clip

    return clip.import_clips(folder).text()


TOOLS = [
    video_info,
    video_scenes,
    video_trim,
    video_subtitles,
    video_contact_sheet,
    video_edit,
    video_highlights,
    video_import,
]


def register(server) -> None:
    for fn in TOOLS:
        server.tool(name=fn.__name__)(fn)


# --------------------------------------------------------------------------- CLI
def _abs(p: str) -> str:
    q = Path(p)
    return str(q.resolve()) if q.exists() else p


def cmd_info(a) -> int:
    from funkbox.video import clip

    print(clip.info(_abs(a.video)).text())
    return 0


def cmd_scenes(a) -> int:
    from funkbox.video import clip

    res = clip.scenes(_abs(a.video), a.threshold, a.min_shot)
    print(res.text(limit=200))
    for k, v in clip.write_scenes(res, a.out, a.name or "").items():
        print(f"{k}: {v}")
    return 0


def cmd_trim(a) -> int:
    from funkbox.video import clip

    p = clip.trim(_abs(a.video), a.start, a.end, a.out, a.name or "", not a.fast, a.duration)
    print(clip.info(p).text())
    print(f"file: {p}")
    return 0


def cmd_subtitles(a) -> int:
    import json
    from funkbox.video import clip

    cues = json.loads(Path(a.cues).read_text(encoding="utf-8-sig")) if a.cues else None
    res = clip.subtitles(_abs(a.video), cues, _abs(a.srt) if a.srt else None, a.style, a.anchor, a.out, a.name or "")
    print(f"{res.cues} cues drawn exactly\nfile: {res.path}\naudit: {res.audit}")
    return 0


def cmd_sheet(a) -> int:
    from funkbox.video import clip

    res = clip.contact_sheet(_abs(a.video), a.every, a.cols, 240, a.name or "", a.out)
    print(f"{res.text()}\nfile: {res.path}")
    return 0


def cmd_edit(a) -> int:
    import json
    from funkbox.video import clip

    cuts = json.loads(Path(a.cuts).read_text(encoding="utf-8-sig"))
    for c in cuts:
        if isinstance(c, dict) and "path" in c:
            c["path"] = _abs(str(c["path"]))
    res = clip.edit(
        cuts,
        transition=a.transition,
        grade=a.grade,
        aspect=a.aspect,
        frame=a.frame,
        target_mb=a.target_mb,
        out_dir=a.out,
        name=a.name or "",
    )
    print(f"{res.text()}\nfile: {res.path}")
    return 0


def cmd_highlights(a) -> int:
    from funkbox.video import clip

    res = clip.highlights([_abs(v) for v in a.videos], tuple(a.target), a.seg, a.out, a.name or "")
    print(f"{res.text()}\nsheet: {res.path}\ncut list: {res.json_path}")
    return 0


def cmd_import(a) -> int:
    from funkbox.video import clip

    print(clip.import_clips(_abs(a.folder)).text())
    return 0


def add_cli(sub) -> None:
    ap = sub.add_parser("video", help="video files: info, scene cuts, trim, burned-in subtitles")
    vs = ap.add_subparsers(
        dest="verb", required=True, metavar="{info,scenes,trim,subtitles,sheet,edit,highlights,import}"
    )
    p = vs.add_parser("info", help="duration, size, fps, codec, audio")
    p.add_argument("video")
    p.set_defaults(fn=cmd_info)
    p = vs.add_parser("scenes", help="find the cuts; a sheet and reel-ready trims")
    p.add_argument("video")
    p.add_argument("--threshold", type=float, default=0.3)
    p.add_argument("--min-shot", type=float, default=0.25)
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_scenes)
    p = vs.add_parser("trim", help="cut a piece into a new file")
    p.add_argument("video")
    p.add_argument("start")
    p.add_argument("end", nargs="?")
    p.add_argument("--duration")
    p.add_argument("--fast", action="store_true", help="stream copy (keyframe start)")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_trim)
    p = vs.add_parser("subtitles", help="burn in subtitles from an .srt/.vtt or a cues JSON")
    p.add_argument("video")
    p.add_argument("--srt")
    p.add_argument("--cues", help="JSON file: [{start, end, text}]")
    p.add_argument("--style", default="lower-third")
    p.add_argument("--anchor", default="bottom")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_subtitles)
    p = vs.add_parser("sheet", help="a grid of frames every N seconds, labelled with their times")
    p.add_argument("video")
    p.add_argument("--every", type=float, default=2.0, help="seconds between frames")
    p.add_argument("--cols", type=int, default=6)
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_sheet)
    p = vs.add_parser("edit", help="render a cut list into one video: 9:16 or 16:9, graded, flash or cut joins")
    p.add_argument("cuts", help='JSON file: [{"path", "start", "end"}, ...]')
    p.add_argument("--transition", default="flash", choices=["flash", "cut"])
    p.add_argument("--grade", default="funky", choices=["funky", "warm", "none"])
    p.add_argument("--aspect", default="9:16", choices=["9:16", "16:9"])
    p.add_argument("--frame", default="pad", choices=["pad", "crop", "blurred-bg"])
    p.add_argument("--target-mb", type=float, default=None, help="aim the bitrate at this file size")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_edit)
    p = vs.add_parser("highlights", help="propose the best moments of videos, ranked by motion, repeats dropped")
    p.add_argument("videos", nargs="+")
    p.add_argument("--target", type=float, nargs=2, default=[30, 60], metavar=("LOW", "HIGH"), help="seconds")
    p.add_argument("--seg", type=float, default=3.0, help="seconds per moment")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_highlights)
    p = vs.add_parser("import", help="copy a folder's video files into the inputs folder, never overwriting")
    p.add_argument("folder")
    p.set_defaults(fn=cmd_import)
