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
    words: str = "",
) -> list:
    """Burn subtitles into a copy of a video. ``cues`` = [{"start": 1.2, "end": 3.0, "text": "..."}] or ``srt`` =
    a .srt/.vtt file. Drawn by funkbox's caption engine in a caption style (lower-third, red-box, bold-pop) with the
    brand's fonts and colours (brand = {primary, secondary, text, font_caption, font_display}); Hebrew reads right
    to left; text is never rewritten and a .text_audit.json proves each cue was drawn exactly.
    ``words`` = a <name>.words.json from video_transcribe: each word then appears when it is said, with the word
    highlight following it. The cue text is still drawn exactly; the transcript only sets the times."""
    from funkbox.video import clip

    res = clip.subtitles(path, cues, srt or None, style, anchor, None, name, brand, words=words or None)
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
    preview: bool = False,
    stabilize: str = "auto",
) -> list:
    """Render a cut list [{"path": ..., "start": ..., "end": ...}, ...] into one video in <out>/video/, for example a
    highlight reel. ``aspect``: 9:16 or 16:9, at 30 fps. ``frame``: pad (the whole picture, bars where it does not
    fill), crop (fills the frame, the edges go) or blurred-bg (bars filled with a blurred copy). ``grade``: funky (more
    colour, a vignette), warm, or none. ``transition``: flash (a white flash between cuts, audio crossfaded) or cut.
    ``stabilize``: auto (default) checks each source for camera shake and steadies only the shaky ones; off keeps the
    picture as shot. ``target_mb`` aims the bitrate at a file size. ``preview`` renders half size in seconds, to check
    the cuts before the final render (the same call without preview). Take the trims from video_scenes or
    video_contact_sheet. Never overwrites."""
    from funkbox.video import clip

    res = clip.edit(
        cuts,
        transition=transition,
        grade=grade,
        aspect=aspect,
        frame=frame,
        target_mb=target_mb,
        name=name,
        preview=preview,
        stabilize=stabilize,
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


@safe
def video_transcribe(path: str, language: str = "", model: str = "", name: str = "") -> list:
    """Speech to text for a video, on this machine (no cloud). Writes every word with its start and end in seconds to
    <out>/video/<name>.words.json (the data captions and cut finders read) and the phrases to <name>.srt. ``language``:
    a code such as en or he; blank to detect. ``model``: tiny, base, small, medium or large-v3. Blank picks by language:
    large-v3 for he (small misses about half the Hebrew words), small for everything else. A bigger model is more
    accurate and slower. The first use of a model downloads it. Never overwrites."""
    from funkbox.video import transcribe as tr

    res = tr.transcribe(_abs(path), language=language or None, model=model or None, name=name)
    return [f"{res.text()}\nwords: {res.path}\nsrt: {res.srt}"]


def _fillers(spec: str) -> tuple[str, ...]:
    """'default' -> cutfind.FILLERS; 'none' or '' -> none; otherwise a comma-separated list of words."""
    from funkbox.video import cutfind

    spec = (spec or "").strip()
    if spec.lower() == "default":
        return cutfind.FILLERS
    if spec.lower() in ("", "none"):
        return ()
    return tuple(w.strip() for w in spec.split(",") if w.strip())


@safe
def video_cutplan(
    path: str, words: str = "", keep_s: float = 0.25, min_pause_s: float = 0.6, fillers: str = "default", name: str = ""
) -> list:
    """Propose cuts for a talking-head video, for a person to approve. Long pauses are found in the sound (a breath,
    ``keep_s`` seconds, is kept on each side); repeated takes are found in the words of a <name>.words.json from
    video_transcribe (the earlier take is proposed for removal, the later one kept), and so are filler words in those
    words: ``fillers`` is "default" (English um, uh, er, ah, hmm and Hebrew אה, אמ, הממ, and their spellings), a comma-separated list of your own, or
    "none" to keep them. Nothing is rendered: this writes
    <out>/video/<name>.cutplan.json with the proposed cuts. After approval, render those cuts with video_edit."""
    from funkbox.video import cutfind

    res = cutfind.cutplan(
        _abs(path),
        words=_abs(words) if words else None,
        keep_s=keep_s,
        min_pause_s=min_pause_s,
        fillers=_fillers(fillers),
        name=name,
    )
    return [res.text()]


@safe
def video_report(path: str, cutplan: str = "", name: str = "") -> list:
    """Acceptance report for a rendered video, written next to it as <name>.report.json and never changing the video.
    Checks loudness against -14 LUFS (within 1 LU), the peak (clipping above -1 dBFS), and the pauses left (quiet of
    0.6 s or more). ``cutplan``: a <name>.cutplan.json from video_cutplan, to check the length against the plan and
    count the cuts removed by reason. A check that cannot be made is reported as not measured, never as a pass."""
    from funkbox.video import report

    res = report.acceptance(_abs(path), cutplan=_abs(cutplan) if cutplan else None, name=name)
    return [res.text()]


TOOLS = [
    video_info,
    video_scenes,
    video_trim,
    video_subtitles,
    video_contact_sheet,
    video_edit,
    video_highlights,
    video_import,
    video_transcribe,
    video_cutplan,
    video_report,
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
    res = clip.subtitles(
        _abs(a.video),
        cues,
        _abs(a.srt) if a.srt else None,
        a.style,
        a.anchor,
        a.out,
        a.name or "",
        words=_abs(a.words) if a.words else None,
    )
    print(f"{res.cues} cues drawn exactly\nfile: {res.path}\naudit: {res.audit}")
    return 0


def cmd_sheet(a) -> int:
    from funkbox.video import clip

    res = clip.contact_sheet(_abs(a.video), a.every, a.cols, 240, a.name or "", a.out)
    print(f"{res.text()}\nfile: {res.path}")
    return 0


def cmd_transcribe(a) -> int:
    from funkbox.video import transcribe as tr

    res = tr.transcribe(_abs(a.path), language=a.language, model=a.model, out_dir=a.out, name=a.name or "")
    print(f"{res.text()}\nwords: {res.path}\nsrt: {res.srt}")
    return 0


def cmd_cutplan(a) -> int:
    from funkbox.video import cutfind

    res = cutfind.cutplan(
        _abs(a.video),
        words=_abs(a.words) if a.words else None,
        keep_s=a.keep,
        min_pause_s=a.min_pause,
        fillers=_fillers(a.fillers),
        out_dir=a.out,
        name=a.name or "",
    )
    print(res.text())
    return 0


def cmd_report(a) -> int:
    from funkbox.video import report

    res = report.acceptance(_abs(a.video), cutplan=_abs(a.cutplan) if a.cutplan else None, name=a.name or "")
    print(res.text())
    return 0 if res.verdict == "pass" else 1


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
        preview=a.preview,
        stabilize=a.stabilize,
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
        dest="verb", required=True, metavar="{info,scenes,trim,subtitles,sheet,edit,highlights,import,transcribe}"
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
    p.add_argument("--words", help="a <name>.words.json from `video transcribe`: each word appears when it is said")
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
    p.add_argument("--preview", action="store_true", help="half size, a few seconds: check the cuts first")
    p.add_argument("--stabilize", default="auto", choices=["auto", "off"], help="auto: steady only the shaky sources")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_edit)
    p = vs.add_parser("transcribe", help="speech to text: every word with its timing, to words.json and an SRT")
    p.add_argument("path")
    p.add_argument("--language", default=None, help="a code such as en or he; omit to detect")
    p.add_argument(
        "--model",
        default=None,
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="omit to pick by language: large-v3 for he, small otherwise",
    )
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_transcribe)
    p = vs.add_parser(
        "cutplan",
        help="propose cuts to approve: long pauses from the sound, repeated takes and fillers from words.json",
    )
    p.add_argument("video")
    p.add_argument("--words", help="a <name>.words.json from `video transcribe`, for repeated takes")
    p.add_argument("--keep", type=float, default=0.25, help="seconds of breath kept on each side of a removed pause")
    p.add_argument("--min-pause", type=float, default=0.6, help="pauses shorter than this stay")
    p.add_argument(
        "--fillers",
        default="default",
        help='filler words to propose from --words: "default" (English um, uh, er, ah, hmm and Hebrew אה, אמ, הממ ...), "none", or a list "um,like"',
    )
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_cutplan)
    p = vs.add_parser("report", help="acceptance report next to a rendered video: loudness, peak, pauses, plan")
    p.add_argument("video")
    p.add_argument("--cutplan", help="a <name>.cutplan.json, to check the length and count the cuts removed")
    p.add_argument("--name", help="report name (default: the video's name)")
    p.set_defaults(fn=cmd_report)
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
