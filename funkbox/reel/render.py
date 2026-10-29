"""Frame composition and encoding: shot -> source+motion -> product -> caption -> transition."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np
from PIL import Image

from funkbox.config import funk_out, resolve_path
from funkbox.gl.recipe import Recipe2, load_recipe
from funkbox.media import encode_video, mux_audio, require_ffmpeg

from . import depth as depthmod
from . import captions as captions_mod
from . import endcard as endcard_mod
from . import framing
from . import motion as motionmod
from . import product as product_mod
from .copydeck import CopyDeck
from .schema import Reel, Shot
from .timeline import ShotSpan

DRAFT_WIDTH, DRAFT_FPS = 540, 24
TRANSITION_SECONDS = {"flash": 0.0, "push": 0.25, "crossfade": 0.3, "zoom-punch": 0.2}
FLASH_FRAMES = 3


def _log(*a) -> None:
    print(*a, file=sys.stderr)


def hex_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


# --------------------------------------------------------------------------- #
# Video source: stream-decoded through ffmpeg (never loads a whole clip into RAM)
# --------------------------------------------------------------------------- #
class VideoStream:
    def __init__(self, path: Path, w: int, h: int, fps: int, trim: tuple[float, float] | None,
                 loop: bool, fit: str = "cover"):
        self.path, self.w, self.h, self.fps, self.trim, self.loop, self.fit = path, w, h, fps, trim, loop, fit
        self.proc: subprocess.Popen | None = None
        self.pos = 0                       # frames consumed
        self.last: np.ndarray | None = None
        self.eof = False

    def _open(self) -> None:
        self.close()
        ff = require_ffmpeg()
        if self.fit == "native":              # framing.py crops/scales each frame itself
            vf = f"fps={self.fps},scale={self.w}:{self.h}"
        elif self.fit == "contain":
            vf = (f"fps={self.fps},scale={self.w}:{self.h}:force_original_aspect_ratio=decrease,"
                  f"pad={self.w}:{self.h}:(ow-iw)/2:(oh-ih)/2:black")
        else:
            vf = (f"fps={self.fps},scale={self.w}:{self.h}:force_original_aspect_ratio=increase,"
                  f"crop={self.w}:{self.h}")
        cmd = [ff, "-loglevel", "error"]
        if self.loop:
            cmd += ["-stream_loop", "-1"]
        if self.trim:
            cmd += ["-ss", f"{self.trim[0]:.3f}"]
        cmd += ["-i", str(self.path)]
        if self.trim:
            cmd += ["-t", f"{self.trim[1] - self.trim[0]:.3f}"]
        cmd += ["-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10 ** 7)
        self.pos, self.eof = 0, False

    def _next(self) -> np.ndarray | None:
        assert self.proc and self.proc.stdout
        n = self.w * self.h * 3
        buf = self.proc.stdout.read(n)
        if len(buf) < n:
            self.eof = True
            return None
        self.pos += 1
        return np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)

    def frame(self, idx: int) -> np.ndarray:
        """Frame ``idx`` (0-based) at the reel fps. Past the end of a non-looping clip: hold the last frame."""
        if self.proc is None or idx < self.pos - 1:
            self._open()
            self.last = None
        while self.pos <= idx and not self.eof:
            f = self._next()
            if f is None:
                break
            self.last = f
        if self.last is None:
            raise RuntimeError(f"could not decode any frame from {self.path}")
        return self.last

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            for s in (self.proc.stdout,):
                if s:
                    s.close()
            self.proc = None


# --------------------------------------------------------------------------- #
@dataclass
class _ShotState:
    video: VideoStream | None = None
    image: motionmod.SourceTex | None = None
    parallax_ok: bool = False
    funk2: Recipe2 | None = None
    extra: dict = field(default_factory=dict)


class Renderer:
    """Composes frames of a reel. Holds one GL context's worth of state; release() when done."""

    def __init__(self, reel: Reel, deck: CopyDeck, spans: list[ShotSpan], *, width: int | None = None,
                 height: int | None = None, fps: int | None = None, depth_mode: str = "auto"):
        st = reel.settings
        self.reel, self.deck, self.spans = reel, deck, spans
        self.src_fps = st.fps
        self.fps = int(fps or st.fps)
        self.w = int(width or st.width)
        self.h = int(height or st.height)
        self.w -= self.w % 2
        self.h -= self.h % 2
        self.depth_mode = depth_mode
        self.motion = motionmod.MotionGL(self.w, self.h)
        self._state: dict[int, _ShotState] = {}
        self._funk_engine = None
        self._last_frames: dict[int, np.ndarray] = {}
        self.audit: dict | None = None            # set to {} to record every drawn string
        self._fonts: captions_mod.Fonts | None = None
        self._endcard: endcard_mod.EndCardRenderer | None = None
        if self.fps != self.src_fps:                 # rescale the timeline to the render fps
            sc = self.fps / self.src_fps
            from dataclasses import replace
            self.spans = [replace(s, start_frame=round(s.start_frame * sc), end_frame=round(s.end_frame * sc))
                          for s in spans]
        self.total_frames = self.spans[-1].end_frame

    # ---------------------------------------------------------------- lookup
    def span_at(self, frame: int) -> ShotSpan:
        for s in self.spans:
            if s.start_frame <= frame < s.end_frame:
                return s
        return self.spans[-1]

    def span_for_shot(self, shot_id: str) -> ShotSpan:
        for s in self.spans:
            if s.id == shot_id:
                return s
        raise KeyError(f"no shot {shot_id!r}; have {[s.id for s in self.spans if s.kind == 'shot']}")

    def _res(self, p: str) -> Path:
        return resolve_path(p, self.reel.base_dir)

    # ---------------------------------------------------------------- sources
    def _source_frame(self, span: ShotSpan, local: int) -> np.ndarray:
        """HxWx3 uint8: the shot's source through its motion, before product/caption."""
        sh: Shot = self.reel.shots[span.index]
        stt = self._state.setdefault(span.index, _ShotState())
        src = sh.source
        if src.kind == "color":
            return np.full((self.h, self.w, 3), hex_rgb(src.value), np.uint8)
        if src.kind == "video":
            if framing.needs_windowing(src, sh.motion):
                return self._windowed_video(span, sh, stt, local)
            if stt.video is None:
                stt.video = VideoStream(self._res(src.value), self.w, self.h, self.fps, src.trim, src.loop, src.fit)
            return stt.video.frame(local)
        if src.kind == "funk2":
            return self._funk2_frame(stt, src.value, local)
        if framing.needs_windowing_image(src, sh.motion):
            return self._windowed_image(span, sh, stt, local)
        # image
        t = local / max(1, span.end_frame - span.start_frame)
        if stt.image is None:
            path = self._res(src.value)
            want = sh.motion.type == "parallax"
            d = depthmod.get_depth(path, self.depth_mode) if want else None
            if want and d is None:
                motionmod._warn(f"shot {sh.id}: no depth available (mode={self.depth_mode}; {depthmod.unavailable_reason() or 'model ok but not used'}); "
                                f"parallax falls back to Ken Burns")
            stt.parallax_ok = d is not None
            if src.fit == "contain":
                stt.image = self._contained(path, d)
            else:
                stt.image = self.motion.load(path, d)
        mode = "parallax" if (sh.motion.type == "parallax" and stt.parallax_ok) else (
            "kenburns" if sh.motion.type in ("kenburns", "parallax") else "none")
        cam = motionmod.camera(sh.motion, t, mode)
        return self.motion.render(stt.image, cam)[..., :3]

    # ---- framed sources (framing.py): crop + scale only, for video and for `frame` motion on stills
    def _frame_u(self, span: ShotSpan, local: int) -> tuple[float, float]:
        n = span.end_frame - span.start_frame
        return local / max(1, n - 1), n / self.fps

    def _window(self, sh: Shot, span: ShotSpan, local: int, sw: int, sh_px: int) -> framing.Window:
        u, dur = self._frame_u(span, local)
        cam = motionmod.camera(sh.motion, local / max(1, span.end_frame - span.start_frame), "kenburns") \
            if sh.motion.type == "kenburns" else None
        return framing.window_for(sh.motion, sh.source, u, dur, sw, sh_px, self.w, self.h, cam)

    def _windowed_video(self, span: ShotSpan, sh: Shot, stt: _ShotState, local: int) -> np.ndarray:
        src = sh.source
        if stt.video is None:
            path = self._res(src.value)
            dw, dh = framing.decode_size(framing.probe_video(path))
            rate = framing.decode_fps(self.fps, src.speed)
            stt.video = VideoStream(path, dw, dh, rate, src.trim, src.loop, "native")
            stt.extra["decode"] = (dw, dh, rate)
        dw, dh, rate = stt.extra["decode"]
        dur = (span.end_frame - span.start_frame) / self.fps
        idx = int(round(framing.source_time(local / self.fps, dur, src.speed) * rate))
        fr = stt.video.frame(idx)
        return framing.draw(fr, self._window(sh, span, local, dw, dh), self.w, self.h, src.pad)

    def _windowed_image(self, span: ShotSpan, sh: Shot, stt: _ShotState, local: int) -> np.ndarray:
        im = stt.extra.get("pil")
        if im is None:
            im = stt.extra["pil"] = Image.open(self._res(sh.source.value)).convert("RGB")
        return framing.draw(im, self._window(sh, span, local, im.width, im.height), self.w, self.h, sh.source.pad)

    def camera_for(self, span: ShotSpan, local: int) -> motionmod.Camera:
        """The camera the shot's source uses at frame ``local`` (for locking a product to it)."""
        sh = self.reel.shots[span.index]
        stt = self._state.get(span.index)
        t = local / max(1, span.end_frame - span.start_frame)
        if sh.source.kind != "image" or framing.needs_windowing_image(sh.source, sh.motion):
            return motionmod.camera(sh.motion, t, "none")
        mode = "parallax" if (sh.motion.type == "parallax" and stt is not None and stt.parallax_ok) else (
            "kenburns" if sh.motion.type in ("kenburns", "parallax") else "none")
        return motionmod.camera(sh.motion, t, mode)

    def _contained(self, path: Path, d: np.ndarray | None) -> motionmod.SourceTex:
        im = Image.open(path).convert("RGB")
        sc = min(self.w / im.width, self.h / im.height)
        nw, nh = max(2, round(im.width * sc)), max(2, round(im.height * sc))
        canvas = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        ox, oy = (self.w - nw) // 2, (self.h - nh) // 2
        canvas.paste(im.resize((nw, nh), Image.LANCZOS), (ox, oy))
        dd = None
        if d is not None:
            dd = np.zeros((self.h, self.w), np.float32)
            dd[oy:oy + nh, ox:ox + nw] = np.asarray(Image.fromarray(d, mode="F").resize((nw, nh), Image.BILINEAR))
        return self.motion.upload(np.asarray(canvas), dd)

    def _funk2_frame(self, stt: _ShotState, path: str, local: int) -> np.ndarray:
        from funkbox.gl.engine import Engine
        if stt.funk2 is None:
            rec = load_recipe(self._res(path))
            rec.text = None                              # text only ever comes from the copy deck
            rec.settings.width, rec.settings.height = self.w, self.h
            stt.funk2 = rec
        if self._funk_engine is None:
            self._funk_engine = Engine(self.w, self.h, ctx=self.motion.ctx)
        rec = stt.funk2
        return self._funk_engine.render(rec, local % rec.settings.frames, grain=True)[..., :3]

    # ---------------------------------------------------------------- composition
    def shot_frame(self, span: ShotSpan, local: int) -> np.ndarray:
        """Composed frame of a shot (source + product + caption), without its transition."""
        if span.kind == "end":
            return self._end_frame(span, local)
        fr = self._source_frame(span, local)
        fr = self._overlay_product(fr, span, local)
        fr = self._overlay_caption(fr, span, local)
        return fr

    def product(self, span: ShotSpan) -> product_mod.ProductCompositor | None:
        sh = self.reel.shots[span.index]
        if sh.product is None:
            return None
        stt = self._state.setdefault(span.index, _ShotState())
        pc = stt.extra.get("product")
        if pc is None:
            pc = stt.extra["product"] = product_mod.ProductCompositor(
                self._res(sh.product.image), sh.product, self.w, self.h)
            if sh.product.match_color > 0:
                bg0 = self._source_frame(span, 0)
                pc.fit_transform(bg0, self.camera_for(span, 0))
        return pc

    def _overlay_product(self, fr: np.ndarray, span: ShotSpan, local: int) -> np.ndarray:
        pc = self.product(span)
        if pc is None:
            return fr
        return pc.draw(fr, self.camera_for(span, local))

    def fonts(self) -> captions_mod.Fonts:
        if self._fonts is None:
            b = self.reel.brand
            self._fonts = captions_mod.Fonts(self._res(b.font_caption), self._res(b.font_display))
        return self._fonts

    def caption_layout(self, span: ShotSpan) -> captions_mod.Layout | None:
        sh = self.reel.shots[span.index]
        if sh.caption is None:
            return None
        stt = self._state.setdefault(span.index, _ShotState())
        lay = stt.extra.get("caption")
        if lay is None:
            c = sh.caption
            lay = stt.extra["caption"] = captions_mod.layout_block(
                c.copy_id, self.deck.text(c.copy_id), captions_mod.load_style(c.style), self.fonts(),
                self.reel.brand, self.w, self.h, anchor=c.anchor, safe_area=self.reel.settings.safe_area)
        return lay

    def caption_sprite(self, span: ShotSpan, local: int) -> captions_mod.Sprite | None:
        lay = self.caption_layout(span)
        if lay is None:
            return None
        c = self.reel.shots[span.index].caption
        assert c is not None
        t = local / self.fps - captions_mod.CAPTION_DELAY
        return captions_mod.render_caption(lay, c.reveal, t, span.vo_dur)

    def _overlay_caption(self, fr: np.ndarray, span: ShotSpan, local: int) -> np.ndarray:
        spr = self.caption_sprite(span, local)
        if spr is None:
            return fr
        if self.audit is not None:
            self._note("caption", span, "caption", "main", self.reel.shots[span.index].caption.copy_id,
                       span.start_frame + local, self.caption_layout(span), spr)
        return spr.over(fr)

    def endcard(self) -> endcard_mod.EndCardRenderer:
        if self._endcard is None:
            self._endcard = endcard_mod.EndCardRenderer(self.reel, self.deck, self.w, self.h, self.fonts(),
                                                        self._res)
        return self._endcard

    def _end_frame(self, span: ShotSpan, local: int) -> np.ndarray:
        def note(it, spr):
            if self.audit is not None:
                self._note("end_card", span, it.slot, it.instance, it.copy_id, span.start_frame + local, it.lay, spr)
        return self.endcard().frame(local / self.fps, note)

    # ---- exactness audit
    def _note(self, kind: str, span: ShotSpan, slot: str, instance: str, copy_id: str, gframe: int,
              lay: captions_mod.Layout, spr: captions_mod.Sprite) -> None:
        assert self.audit is not None
        key = f"{span.id}/{slot}/{instance}"
        e = self.audit.get(key)
        scale = self.src_fps / self.fps
        f0 = int(round(gframe * scale))
        if e is None:
            e = self.audit[key] = {"kind": kind, "shot": span.id, "slot": slot, "instance": instance,
                                   "copy_id": copy_id, "first_frame": f0, "last_frame": f0, "chars": -1,
                                   "text": "", "complete_frame": None, "drawn_lines": []}
        e["last_frame"] = f0
        if spr.n > e["chars"] or e["chars"] < 0:
            e["chars"] = spr.n
            e["drawn_lines"] = [s for s in spr.drawn]
            e["text"] = lay.reconstruct(spr.drawn)
        if e["complete_frame"] is None and e["text"] == lay.text:
            e["complete_frame"] = f0

    def audit_report(self) -> list[dict]:
        """Entries for every string drawn so far: copy id, the exact text drawn, frame range."""
        out = []
        for e in (self.audit or {}).values():
            d = {k: v for k, v in e.items() if k != "chars"}
            d["text_exact"] = e["text"] == self.deck.text(e["copy_id"])
            d["frames"] = [e["first_frame"], e["last_frame"]]
            out.append(d)
        return out

    def frame(self, f: int) -> np.ndarray:
        """Final frame ``f`` (transition applied)."""
        span = self.span_at(f)
        local = f - span.start_frame
        cur = self.shot_frame(span, local)
        self._last_frames[span.index] = cur
        if span.kind == "shot" and span.index > 0:
            sh = self.reel.shots[span.index]
            cur = self._transition(sh.transition_in, span, local, cur)
        return cur

    def _prev_last(self, span: ShotSpan) -> np.ndarray:
        prev = self.spans[span.index - 1]
        hit = self._last_frames.get(prev.index)
        if hit is not None:
            return hit
        hit = self.shot_frame(prev, prev.end_frame - prev.start_frame - 1)
        self._last_frames[prev.index] = hit
        return hit

    def _transition(self, kind: str, span: ShotSpan, local: int, cur: np.ndarray) -> np.ndarray:
        if kind == "cut":
            return cur
        if kind == "flash":
            if local >= FLASH_FRAMES:
                return cur
            a = 1.0 - local / FLASH_FRAMES
            return np.clip(cur * (1 - a) + 255.0 * a + 0.5, 0, 255).astype(np.uint8)
        n = max(1, round(TRANSITION_SECONDS[kind] * self.fps))
        n = min(n, span.end_frame - span.start_frame)
        if local >= n:
            return cur
        p = (local + 1) / n
        if kind == "zoom-punch":
            s = 1.08 - 0.08 * p
            return _zoom_about_center(cur, s)
        prev = self._prev_last(span)
        if kind == "crossfade":
            return np.clip(prev * (1 - p) + cur * p + 0.5, 0, 255).astype(np.uint8)
        # push: the incoming shot slides up over the outgoing one (which moves up with it)
        off = int(round((1 - p) * self.h))
        if off <= 0:
            return cur
        out = np.empty_like(cur)
        out[:off] = prev[self.h - off:]
        out[off:] = cur[: self.h - off]
        return out

    # ---------------------------------------------------------------- API
    def still(self, shot_id: str, t: float) -> np.ndarray:
        """Frame of ``shot_id`` at ``t`` seconds into it (transition included)."""
        span = self.span_for_shot(shot_id)
        local = int(round(t * self.fps))
        local = max(0, min(local, span.end_frame - span.start_frame - 1))
        return self.frame(span.start_frame + local)

    def frames(self) -> Iterator[np.ndarray]:
        for f in range(self.total_frames):
            yield self.frame(f)

    def release(self) -> None:
        for s in self._state.values():
            if s.video:
                s.video.close()
        self._state.clear()
        if self._funk_engine is not None:
            self._funk_engine.release()
            self._funk_engine = None
        self.motion.release()


def _zoom_about_center(fr: np.ndarray, s: float) -> np.ndarray:
    h, w = fr.shape[:2]
    cw, ch = w / s, h / s
    x0, y0 = (w - cw) / 2, (h - ch) / 2
    im = Image.fromarray(fr).transform((w, h), Image.AFFINE, (cw / w, 0, x0, 0, ch / h, y0), Image.BILINEAR)
    return np.asarray(im)


# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    reel: Reel
    deck: CopyDeck
    spans: list[ShotSpan]
    report: "object"


def prepare(src, base_dir=None, fmt: str | None = None) -> Plan:
    """Validate a reel (dict / path / Reel) and build its timeline. ``fmt`` picks one of the reel's formats.
    Raises ReelError listing problems."""
    from .schema import ReelError
    from .validate import validate
    rep = validate(src, base_dir, fmt=fmt)
    if not rep.ok or rep.reel is None or rep.deck is None:
        raise ReelError(rep.errors or ["invalid reel"])
    return Plan(rep.reel, rep.deck, rep.spans, rep)


# --------------------------------------------------------------------------- #
# Full render
# --------------------------------------------------------------------------- #
@dataclass
class RenderOutput:
    paths: dict[str, str]
    frames: int
    width: int
    height: int
    fps: int
    seconds: float                    # wall time
    duration: float                   # reel length in seconds
    shots: int
    audit_ok: bool | None = None
    warnings: list[str] = field(default_factory=list)
    end_card: bool = True

    def report_line(self) -> str:
        return (f"{self.duration:.2f}s, {self.shots} shots{' + end card' if self.end_card else ''}, "
                f"{self.width}x{self.height} @ {self.fps} fps, "
                f"{self.frames} frames rendered in {self.seconds:.1f}s")


def draft_size(reel: Reel) -> tuple[int, int]:
    w = DRAFT_WIDTH
    return w, round(w * reel.settings.height / reel.settings.width) // 2 * 2


def render_reel(src, name: str, *, base_dir=None, out_dir: str | Path | None = None, draft: bool = False,
                audit: bool = False, depth_mode: str | None = None, with_audio: bool = True,
                gif: bool = False, progress: bool = False, plan: Plan | None = None) -> RenderOutput:
    """Render a reel to ``<out_dir>/<name>.mp4`` (+ poster PNG, optional text audit JSON).

    ``draft``: 540 px wide, 24 fps, no depth model (parallax degrades to Ken Burns).
    """
    plan = plan or prepare(src, base_dir)
    reel, deck, spans = plan.reel, plan.deck, plan.spans
    out_dir = Path(out_dir) if out_dir else funk_out()
    out_dir.mkdir(parents=True, exist_ok=True)
    if draft:
        w, h = draft_size(reel)
        fps, dm = DRAFT_FPS, "off"
    else:
        w, h, fps, dm = reel.settings.width, reel.settings.height, reel.settings.fps, (depth_mode or "auto")
    if depth_mode and draft:
        dm = depth_mode
    t0 = time.perf_counter()
    r = Renderer(reel, deck, spans, width=w, height=h, fps=fps, depth_mode=dm)
    if audit:
        r.audit = {}
    paths: dict[str, str] = {}
    try:
        def gen() -> Iterator[np.ndarray]:
            n = r.total_frames
            for f in range(n):
                if progress and f % 24 == 0:
                    _log(f"  frame {f}/{n}")
                yield r.frame(f)

        silent = out_dir / f"{name}.video.mp4" if with_audio else out_dir / f"{name}.mp4"
        paths = encode_video(gen(), r.w, r.h, fps, silent, poster=out_dir / f"{name}.png", gif=gif)
        final = out_dir / f"{name}.mp4"
        audio_path = None
        if with_audio:
            audio_path = _build_audio(plan, out_dir, name, fps)
            if audio_path is not None:
                mux_audio(silent, audio_path, final)
                silent.unlink(missing_ok=True)
                paths["audio"] = str(audio_path)
            else:
                silent.replace(final)
            paths["mp4"] = str(final)
        paths.pop("video", None)
        audit_ok = None
        if audit:
            rep = _audit_json(r, plan, name, out_dir)
            paths["text_audit"] = rep["path"]
            audit_ok = rep["ok"]
        frames, W, Hh = r.total_frames, r.w, r.h
    finally:
        r.release()
    return RenderOutput(paths, frames, W, Hh, fps, time.perf_counter() - t0, frames / fps, len(reel.shots),
                        audit_ok, plan.report.warnings if plan.report is not None else [], reel.end_card is not None)


def _build_audio(plan: Plan, out_dir: Path, name: str, fps: int) -> Path | None:
    try:
        from . import audio
    except ImportError:
        return None
    return audio.build_mix(plan, out_dir, name)


def _audit_json(r: Renderer, plan: Plan, name: str, out_dir: Path) -> dict:
    """Write <name>.text_audit.json: every string drawn, its copy id and frame range."""
    entries = sorted(r.audit_report(), key=lambda e: (e["first_frame"], e["shot"], e["slot"], e["instance"]))
    expected = set()
    for sh in plan.reel.shots:
        if sh.caption:
            expected.add(f"{sh.id}/caption/main")
    ec = plan.reel.end_card
    if ec:
        for it in r.endcard().items:
            expected.add(f"end_card/{it.slot}/{it.instance}")
    seen = {f"{e['shot']}/{e['slot']}/{e['instance']}" for e in entries}
    missing = sorted(expected - seen)
    inexact = [f"{e['shot']}/{e['slot']}/{e['instance']}" for e in entries if not e["text_exact"]]
    incomplete = [f"{e['shot']}/{e['slot']}/{e['instance']}" for e in entries if e["complete_frame"] is None]
    ok = not missing and not inexact
    deck = str(plan.deck.path)
    try:                                   # relative to the audit file, so audits can be committed and shared
        deck = os.path.relpath(deck, out_dir).replace(os.sep, "/")
    except ValueError:                     # another drive on Windows
        pass
    doc = {"reel": name, "copy_deck": deck, "fps": plan.reel.settings.fps,
           "ok": ok, "missing": missing, "inexact": inexact, "never_fully_typed": incomplete,
           "entries": entries}
    path = out_dir / f"{name}.text_audit.json"
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"path": str(path), "ok": ok}


def render_formats(src, name: str, *, base_dir=None, fmt: str | None = None, **kw) -> list[RenderOutput]:
    """Render ``fmt``, or every format the reel declares (``<name>-<format>.mp4``), or just the reel's own size
    when it declares none. ``kw`` goes to render_reel (draft, audit, out_dir, ...)."""
    from .validate import formats_of
    ids: list[str | None] = [fmt] if fmt else (formats_of(src, base_dir) or [None])
    outs = []
    for f in ids:
        plan = prepare(src, base_dir, f)
        outs.append(render_reel(None, f"{name}-{f}" if f else name, plan=plan, **kw))
    return outs
