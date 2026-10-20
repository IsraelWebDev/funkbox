"""auto_reel: build <slug>.reel.json for a project, deterministically (seeded by the slug).

Order: hook (first copy line) -> body lines -> end card (ids starting ``end_`` plus ``url``, or ``cta_ids``).
Only APPROVED, locked copy is used (project.copy_lock_error); shots reference copy by id only, like every reel.
"""
from __future__ import annotations

import json
import math
import random
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from funkgl.recipe import resolve_path

from . import audio, brand as brandmod, captions, collision, copydeck, depth
from . import project as P
from .schema import Brand, ReelError
from .timeline import VO_LEAD, VO_TAIL

W, H, FPS = 1080, 1920, 30
END_SECONDS = 4.0
MAX_CLIP_SHARE = 0.40
PACE_SECONDS = {"fast": 1.3, "calm": 2.2}
MIN_SHOT = 0.8
PRODUCT_H = 0.38
PRODUCT_Y = 0.78                         # bottom anchor; the reels UI owns the bottom 22%
SHRINK = 0.85
PLACEMENTS = (("bottom-centre", 0.5), ("right-third", 0.7), ("left-third", 0.3))
FAST_TRANSITIONS = ("cut", "zoom-punch", "push", "cut", "flash")
FILLER_MIN_VISUALS = 4
FILLER_SHADERS = ("warp", "plasma", "fbm_flow", "blobs", "moire", "rings")
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VID_EXT = {".mp4", ".mov", ".webm"}
AUD_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
HOLD = 0.35                              # a finished caption stays up at least this long


@dataclass
class AutoResult:
    slug: str
    reel: dict
    path: Path
    report: object = None                # validate.Report
    storyboard: Path | None = None
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def text(self) -> str:
        s = self.stats
        out = [f"auto_reel for {self.slug!r}: {self.path}",
               f"{s.get('shots')} shots + end card, {s.get('total', 0):.1f}s total, product in {s.get('product_shots')} "
               f"of {s.get('shots')} shots, {s.get('clip_shots')} clip shot(s), motion {s.get('motion')}, "
               f"voice-over {'on' if s.get('voiceover') else 'off'}, music {s.get('music') or 'none'}"]
        out += [f"note: {n}" for n in self.notes]
        out.append("validate: " + (self.report.text() if self.report is not None else "?"))
        if self.storyboard:
            out.append(f"storyboard: {self.storyboard}")
        return "\n".join(out)


# --------------------------------------------------------------------------- #
def _roles(deck: copydeck.CopyDeck, cta_ids: list[str] | None, has_logo: bool) -> dict:
    ids = list(deck.lines)
    if cta_ids:
        bad = [c for c in cta_ids if c not in deck.lines]
        if bad:
            raise ReelError([f"cta_ids {bad} are not in the approved copy (have {ids})"])
        end_ids = list(dict.fromkeys(cta_ids))
    else:
        end_ids = [i for i in ids if i.startswith("end_")]
        if "url" in deck.lines and "url" not in end_ids:
            end_ids.append("url")
    rest = [i for i in ids if i not in end_ids]
    if not rest:
        raise ReelError(["the approved copy has no hook/body line: add at least one line that is not an end_* line "
                         "(the first line is the hook)"])
    hook, body = rest[0], rest[1:]
    end = None
    if end_ids:
        non_url = [i for i in end_ids if i != "url"]
        url = "url" if "url" in end_ids else None
        head = next((i for i in non_url if re.search(r"head", i)), non_url[0] if non_url else None)
        others = [i for i in non_url if i != head]
        sub = next((i for i in others if re.search(r"sub", i)), others[0] if others else None)
        if cta_ids and len(cta_ids) >= 2 and "url" not in cta_ids:       # explicit order: headline, sub, url
            head, sub, url = (list(cta_ids) + [None, None])[:3]
        if head and sub and url:
            end = {"template": "brand-split", "copy": {"headline": head, "sub": sub, "url": url}}
        elif head and url and has_logo:
            end = {"template": "centered-logo", "copy": {"headline": head, "url": url}}
        else:
            raise ReelError([
                "cannot build an end card from the copy ids " + str(end_ids) + ": the 'brand-split' end card needs "
                "three lines (end_head, end_sub, url); 'centered-logo' needs end_head and url plus a brand logo. "
                "Add the missing lines with propose_copy (the user approves them), or pass cta_ids."])
    return {"hook": hook, "body": body, "end": end, "end_ids": end_ids}


def _clip_duration(path: Path) -> float | None:
    try:
        import funk
        r = subprocess.run([funk.ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                           timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:  # noqa: BLE001
        pass
    return None


def make_fillers(d: Path, brand: dict, count: int, rng: random.Random) -> list[str]:
    """funk2 recipes tinted with the brand palette: a brand-coloured gradient PNG multiplied into an animated
    shader (no text). Returns project-relative recipe paths."""
    out = []
    pr, sc = np.array(brandmod.hex_to_rgb(brand["primary"]), float), np.array(brandmod.hex_to_rgb(brand["secondary"]), float)
    light = pr + (255 - pr) * 0.45
    shaders = list(FILLER_SHADERS)
    rng.shuffle(shaders)
    fdir = d / "funk2"
    fdir.mkdir(parents=True, exist_ok=True)
    w, h = 270, 480
    yy, xx = np.mgrid[0:h, 0:w]
    for j in range(count):
        ang = rng.uniform(0, math.pi)
        t = ((xx / w - 0.5) * math.cos(ang) + (yy / h - 0.5) * math.sin(ang)) + 0.5
        t = np.clip(t, 0, 1)[..., None]
        mid = np.clip(0.5 + 0.5 * np.sin((t * 2.0 + j) * math.pi), 0, 1)
        img = (pr * (1 - t) + light * t) * (1 - 0.5 * mid) + sc * (0.5 * mid) * 0.6 + pr * (0.5 * mid) * 0.4
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(fdir / f"filler-{j + 1}-bg.png")
        recipe = {"version": 2, "settings": {"width": 540, "height": 960, "fps": 30, "seconds": 2, "seed": 7 + j},
                  "palette": {"name": "mono", "cycles": 1},
                  "layers": [{"type": "shader", "shader": f"shaders/{shaders[j % len(shaders)]}.frag"},
                             {"type": "image", "path": f"filler-{j + 1}-bg.png", "fit": "cover", "blend": "multiply",
                              "opacity": 1.0}],
                  "post": [{"name": "vignette", "amount": 0.3}]}
        (fdir / f"filler-{j + 1}.json").write_text(json.dumps(recipe, indent=2) + "\n", encoding="utf-8")
        out.append(f"funk2/filler-{j + 1}.json")
    return out


# --------------------------------------------------------------------------- #
def auto_reel(slug: str, length_s: float = 20.0, style: str = "red-box", pace: str = "fast",
              cta_ids: list[str] | None = None, music: str | None = None) -> AutoResult:
    d = P.project_dir(slug)
    notes: list[str] = []
    err = P.copy_lock_error(slug)
    if err:
        raise ReelError([f"auto_reel needs approved copy: {err}"])
    if pace not in PACE_SECONDS:
        raise ReelError([f"pace must be one of {sorted(PACE_SECONDS)}"])
    if style not in captions.list_styles():
        raise ReelError([f"unknown caption style {style!r}; have {captions.list_styles()}"])
    if isinstance(length_s, bool) or not isinstance(length_s, (int, float)) or not 8 <= length_s <= 60:
        raise ReelError(["length_s must be between 8 and 60 seconds"])
    meta = P.load_meta(slug)
    br = meta.get("brand")
    if not br:
        if (d / "product" / "product.png").is_file() or (d / "brand" / "logo.png").is_file():
            br = brandmod.brand_from_assets(slug)
            notes.append("no brand was set yet; brand_from_assets was run automatically (override with set_brand)")
        else:
            br = {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff",
                  "font_caption": brandmod.DEFAULT_CAPTION_FONT, "font_display": brandmod.DEFAULT_DISPLAY_FONT,
                  "logo": None}
            notes.append("no product or logo to take colours from: default brand colours used (set_brand to change)")
    deck = copydeck.load_copy(d / "copy.json")
    rng = random.Random(slug)

    stills = [p for p in P.files_in(d / "shots") if p.suffix.lower() in IMG_EXT]
    clips = [p for p in P.files_in(d / "clips") if p.suffix.lower() in VID_EXT]
    prod_path = d / "product" / "product.png"
    has_product = prod_path.is_file()
    logo = br.get("logo") if br.get("logo") and (d / br["logo"]).is_file() else None
    roles = _roles(deck, cta_ids, bool(logo))
    end = roles["end"]
    if not has_product:
        notes.append("no product image: shots have no product (ingest one, or set_product)")

    # ---- assets: stills and clips; funk2 filler when there are fewer than 4 visuals
    visuals: list[dict] = [{"kind": "image", "path": p.relative_to(d).as_posix()} for p in stills]
    n_real = len(stills) + len(clips)
    filler_n = max(0, FILLER_MIN_VISUALS - n_real)
    filler_paths: list[str] = []
    if filler_n:
        filler_paths = make_fillers(d, br, filler_n, rng)
        visuals += [{"kind": "funk2", "path": p} for p in filler_paths]
        notes.append(f"only {n_real} visual asset(s) in shots/ and clips/: added {filler_n} funk2 filler recipe(s) in "
                     f"the brand colours (funk2/). Drop more scene images or clips in the inbox for a richer ad.")
    # ---- how many shots
    music_file = None
    mus = [p for p in P.files_in(d / "music") if p.suffix.lower() in AUD_EXT]
    if music:
        sel = [p for p in mus if p.name == music or p.stem == music]
        if not sel:
            raise ReelError([f"no music file {music!r} in music/ (have {[p.name for p in mus]})"])
        music_file = sel[0]
    elif mus:
        music_file = max(mus, key=lambda p: (_clip_duration(p) or 0.0, p.name))      # the longest track
        if len(mus) > 1:
            notes.append(f"{len(mus)} music files: using the longest, {music_file.name} (pick another with music=)")
    end_total = END_SECONDS if end else 0.0
    T = float(length_s) - end_total

    # ---- fonts / VO, then how long every copy line needs (caption reveal and voice-over)
    fonts = captions.Fonts(resolve_path(br["font_caption"], d), resolve_path(br["font_display"], d))
    bobj = Brand(primary=br["primary"], secondary=br["secondary"], text=br["text"])
    vo_on = audio.tts_mode() != "none" and len(deck.lines) > 0
    if not vo_on:
        notes.append("voice-over off: " + audio.tts_unavailable_reason())
    cap_ids = [roles["hook"]] + roles["body"]
    reveal_for = {roles["hook"]: "typewriter"}
    for j, cid in enumerate(roles["body"]):
        reveal_for[cid] = "words" if j % 2 == 0 else "typewriter"
    vo_len = {cid: audio.wav_duration(audio.synthesize(deck, cid)) for cid in cap_ids} if vo_on else {}
    cap_dur: dict[str, float] = {}
    for cid in cap_ids:
        lay = captions.layout_block(cid, deck.text(cid), captions.load_style(style), fonts, bobj, W, H,
                                    anchor="bottom", safe_area="reels")
        nd = max(MIN_SHOT, captions.CAPTION_DELAY + captions.reveal_seconds(lay, reveal_for[cid], vo_len.get(cid)) + HOLD)
        if cid in vo_len:
            nd = max(nd, vo_len[cid] + VO_LEAD + VO_TAIL + 0.05)
        cap_dur[cid] = nd
    # captioned shots take what their lines need; the remaining time is shared by uncaptioned shots whose
    # count follows the pace (and which are never shorter than min_free)
    min_free = 0.9 if pace == "fast" else 1.5
    free_time = T - sum(cap_dur.values())
    if free_time >= min_free:
        n_free = round(free_time / PACE_SECONDS[pace])
        n_free = max(math.ceil(free_time / 3.0), min(n_free, int(free_time // min_free)), 1)
    else:
        n_free = 0
    n = len(cap_ids) + n_free
    if n < 2:
        n = 2
        n_free = 1
    if not n_free and free_time > 0:                   # no room for extra shots: the lines' shots stretch a little
        for cid in cap_dur:
            cap_dur[cid] += free_time / len(cap_ids)
    if free_time < -2.0:
        raise ReelError([f"the {len(cap_ids)} copy lines need {sum(cap_dur.values()):.1f}s (caption reveals and "
                         f"voice-over) but length_s={length_s:g} leaves {T:.1f}s; raise length_s or shorten/cut lines"])

    # ---- visual per shot: clips at most 40% of the shots, stills (and filler) cycle through the rest
    max_clips = int(math.floor(MAX_CLIP_SHARE * n))
    clip_pool = list(clips)
    rng.shuffle(clip_pool)
    if visuals:
        c = min(max_clips, len(clip_pool))
        if len(clip_pool) > c:
            notes.append(f"{len(clip_pool)} clips supplied but at most 40% of the {n} shots (= {c}) use clips")
    else:                                   # only clips: the cap cannot apply
        c = n
        notes.append("only video clips were supplied: the 40% clip cap cannot apply")
    clip_items = [{"kind": "video", "path": p.relative_to(d).as_posix(), "file": p} for p in clip_pool[:c]]
    clip_slots = {min(n - 1, max(1, round((j + 0.5) * n / c))) for j in range(c)} if (c and visuals) else \
        (set(range(n)) if c else set())
    cycle = list(visuals)
    rng.shuffle(cycle)
    seq: list[dict] = []
    si = ci = 0
    for i in range(n):
        if i in clip_slots and clip_items:
            seq.append(clip_items[ci % len(clip_items)])
            ci += 1
        else:
            pool = cycle or clip_items
            seq.append(pool[si % len(pool)])
            si += 1

    # ---- captions per shot
    cap_at: dict[int, str] = {0: roles["hook"]}
    m = len(roles["body"])
    for j, cid in enumerate(roles["body"]):
        cap_at[1 + (j * (n - 1)) // m] = cid

    # ---- product placement
    prod_slots = [i for i in range(n) if i % 2 == 0 or i == n - 1] if has_product else []
    asp = collision.image_aspect(prod_path) if has_product else 1.0
    shots: list[dict] = []
    place_k = 0
    use_parallax = depth.available()
    motion_kind = "parallax" if use_parallax else "kenburns"
    if not use_parallax:
        notes.append(f"no depth model ({depth.unavailable_reason()}): stills use Ken Burns instead of parallax")
    sgn = 1
    for i in range(n):
        vis = seq[i]
        sh: dict = {"id": f"s{i + 1}"}
        if vis["kind"] == "video":
            sh["source"] = {"video": vis["path"]}
            sh["motion"] = {"type": "none"}
        elif vis["kind"] == "funk2":
            sh["source"] = {"funk2": vis["path"]}
            sh["motion"] = {"type": "none"}
        else:
            sh["source"] = {"image": vis["path"]}
            if use_parallax:
                if i % 2 == 0:
                    sh["motion"] = {"type": "parallax", "push": 0.08, "orbit_deg": 0, "dof": 0.25}
                else:
                    sgn = -sgn
                    sh["motion"] = {"type": "parallax", "push": 0.03, "orbit_deg": 3 * sgn, "dof": 0.25}
            else:
                if i % 2 == 0:
                    sh["motion"] = {"type": "kenburns", "zoom": [1.0, 1.12], "pan": [[0, 0], [0.02, -0.03]]}
                else:
                    sh["motion"] = {"type": "kenburns", "zoom": [1.12, 1.0], "pan": [[0.02, 0.0], [-0.02, 0.01]]}
        if i in prod_slots:
            name, x = PLACEMENTS[place_k % len(PLACEMENTS)]
            place_k += 1
            h_frac = PRODUCT_H
            if h_frac * H * asp > 0.9 * W:                      # very wide product: keep it inside the frame
                h_frac = 0.9 * W / (H * asp)
            half = h_frac * H * asp / 2 / W
            x = min(max(x, half + 0.03), 1 - half - 0.03)
            sh["product"] = {"image": "product/product.png", "x": round(x, 3), "y": PRODUCT_Y, "height": round(h_frac, 3),
                             "depth": 0.85, "shadow": 0.6, "match_color": 0.5}
        cid = cap_at.get(i)
        if cid:
            anchor_pref = ("top" if (i == 0 or i % 2) else "bottom")
            sh["caption"] = {"copy_id": cid, "style": style, "reveal": reveal_for[cid], "anchor": anchor_pref}
            if "product" in sh:
                _resolve_collision(sh, deck, style, fonts, bobj, asp)
            if vo_on:
                sh["vo"] = cid
        if i == 0:
            sh["transition_in"] = "cut"
        elif pace == "calm":
            sh["transition_in"] = "crossfade"
        else:
            sh["transition_in"] = FAST_TRANSITIONS[i % len(FAST_TRANSITIONS)]
        shots.append(sh)

    # ---- durations
    free_each = max(0.0, free_time) / n_free if n_free else 0.0
    dur = [cap_dur[sh["caption"]["copy_id"]] if "caption" in sh else free_each for sh in shots]
    total = sum(dur) + end_total
    if abs(total - length_s) > 2.0:
        raise ReelError([f"the lines need {total:.1f}s (voice-over and caption reveals) but length_s={length_s:g}; "
                         f"raise length_s, shorten lines, or use pace=calm with fewer lines"])
    for sh, x in zip(shots, dur):
        sh["duration"] = round(max(0.2, round(x * FPS) / FPS), 3)
    # clips: loop when shorter than the shot, trim when longer is not needed (the shot cuts them)
    for sh, vis in zip(shots, seq):
        if vis["kind"] == "video":
            cl = _clip_duration(vis["file"])
            if cl is not None and cl < sh["duration"] + 0.1:
                sh["source"]["loop"] = True

    snap = bool(music_file and pace == "fast")
    if music_file:
        try:
            audio.detect_beats(music_file)
        except Exception as e:  # noqa: BLE001 - too short / silent for a tempo: keep the music, drop beat snapping
            snap = False
            notes.append(f"{music_file.name}: no usable beat ({e}); cuts are not snapped to beats")
        mlen = _clip_duration(music_file)
        if mlen is not None and mlen + 0.5 < sum(dur) + end_total:
            notes.append(f"{music_file.name} is {mlen:.1f}s, shorter than the {sum(dur) + end_total:.1f}s ad: the music "
                         f"ends early (drop a longer track in the inbox)")

    reel = {"version": 3, "settings": {"width": W, "height": H, "fps": FPS, "safe_area": "reels"},
            "brand": {"primary": br["primary"], "secondary": br["secondary"], "text": br["text"],
                      "font_caption": br["font_caption"], "font_display": br["font_display"]},
            "copy": "copy.json",
            "audio": {"music_gain_db": -14, "voiceover": bool(vo_on), "duck_db": -10,
                      "snap_cuts_to_beats": snap, "loudness_lufs": -14},
            "shots": shots}
    if logo:
        reel["brand"]["logo"] = logo
    if music_file:
        reel["audio"]["music"] = music_file.relative_to(d).as_posix()
    if end:
        ec = {"template": end["template"], "duration": END_SECONDS, "audio": "silence", "copy": end["copy"],
              "headline_reveal": "typewriter"}
        if has_product:
            ec["product"] = "product/product.png"
        reel["end_card"] = ec
    path = d / f"{slug}.reel.json"
    path.write_text(json.dumps(reel, indent=2) + "\n", encoding="utf-8")

    from .validate import validate
    rep = validate(str(path))
    if not rep.ok:
        raise ReelError([f"auto_reel built a reel that does not validate (kept at {path}):"] + rep.errors)
    P.advance_status(slug, "reel-built")
    n_prod = sum(1 for s in shots if "product" in s)
    n_clip = sum(1 for s in shots if "video" in s["source"])
    res = AutoResult(slug, reel, path, rep, None, notes, {
        "shots": n, "total": rep.info.get("total_seconds", total), "product_shots": n_prod, "clip_shots": n_clip,
        "filler": len(filler_paths), "voiceover": vo_on, "music": music_file.name if music_file else None,
        "motion": motion_kind, "hook": roles["hook"], "end_card": (end or {}).get("copy")})
    return res


def _resolve_collision(sh: dict, deck: copydeck.CopyDeck, style: str, fonts, brand: Brand, asp: float) -> None:
    """Move the caption anchor (top <-> bottom) when it would overlap the product by more than 8% of the caption
    area; if both anchors collide shrink the product to 0.85x once, then give up with an error."""
    c, p = sh["caption"], sh["product"]
    st = captions.load_style(c["style"])
    text = deck.text(c["copy_id"])

    def worst(anchor: str, ph: float) -> float:
        lay = captions.layout_block(c["copy_id"], text, st, fonts, brand, W, H, anchor=anchor, safe_area="reels")
        pb = collision.product_box(p["x"], p["y"], ph, asp, W, H)
        return collision.overlap_fraction(collision.caption_box(lay), pb)

    order = [c["anchor"], "bottom" if c["anchor"] == "top" else "top"]
    for attempt in range(2):
        for anchor in order:
            if worst(anchor, p["height"]) <= collision.MAX_OVERLAP:
                c["anchor"] = anchor
                return
        if attempt == 0:
            p["height"] = round(p["height"] * SHRINK, 3)
    raise ReelError([f"shot {sh['id']}: caption {c['copy_id']!r} collides with the product at both the top and the "
                     f"bottom anchor, even with the product shrunk to {SHRINK}x; shorten that line"])
