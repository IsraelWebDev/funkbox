#!/usr/bin/env python3
"""
funk v3 "reel" as an MCP server: shot list in, vertical video ad out.

Register (Claude Code):
    claude mcp add reel -e FUNK_OUT=C:\\dev\\funkbox\\out -- C:\\dev\\funkbox\\.venv\\Scripts\\python.exe C:\\dev\\funkbox\\reel_mcp.py
Env: FUNK_OUT, FUNK_INPUTS, FUNK_HOME, FUNK_GL_BACKEND (egl|default), FUNK_MODELS.

NOTHING may be printed to stdout here: stdout is the MCP protocol. Logs go to stderr.
Copy: propose_copy writes only copy.draft.json; copy.json is written solely by approve_copy after the user
approves the exact listing (MCP elicitation click, or an explicit chat confirmation when the client has none).
"""
from __future__ import annotations

import functools
import io
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PIL import Image as PILImage

try:  # mcp >= 2
    from mcp.server.mcpserver import Context, Image, MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP as _Server, Image  # type: ignore
from typing import Literal  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from funkgl.context import GLUnavailable  # noqa: E402
from funkgl.recipe import funk_home, funk_inputs, funk_out, resolve_path  # noqa: E402
from reel import approval, autoreel, brand as brandmod, captions, copydeck, endcard, ingest as ingestmod, render  # noqa: E402
from reel import project as projmod  # noqa: E402
from reel.schema import ANCHORS, MOTION_TYPES, REVEALS, TRANSITIONS, ReelError  # noqa: E402
from reel.storyboard import storyboard as _storyboard  # noqa: E402
from reel.validate import validate as _validate  # noqa: E402

OUT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
MAX_SECONDS = 60.0

INSTRUCTIONS = (
    "On-screen and spoken text come only from the copy deck by ID. If the needed line doesn't exist, "
    "ask the user to add it to copy.json; never invent copy. "
    "Make an ad from dropped files: new_project, then ingest what the user put in the inbox folder (or a path they "
    "name), brand_from_assets, and propose_copy with wording drawn from the user's brief (ids hook, body lines, "
    "end_head, end_sub, url). "
    "Show propose_copy's numbered listing to the user verbatim and call approve_copy: only lines the user approves "
    "(hash-locked in copy.json) can ever reach pixels or audio, and no tool lets you write copy.json yourself. "
    "Then auto_reel builds the shot list and a storyboard to look at; fix problems with set_product, set_brand or "
    "by asking for more assets, and use project_render with draft=true before the full render. "
    "validate, storyboard, still and render also take project=<name> instead of a reel dict; reels reference copy "
    "lines by copy_id, never literal text, and the product image is composited, never generated."
)

server = _Server("reel", instructions=INSTRUCTIONS)


def _log(*a) -> None:
    print(*a, file=sys.stderr)


_GL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reel-gl")


def on_gl(fn: Callable, *a, **k):
    """GL contexts are thread-affine: every tool body runs on ONE dedicated thread."""
    return _GL.submit(lambda: fn(*a, **k)).result()


def _png(img: PILImage.Image, max_side: int = 900) -> Image:
    img = img.copy()
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return Image(data=buf.getvalue(), format="png")


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except GLUnavailable as e:
            return f"ERROR: no OpenGL context available. {e}"
        except ReelError as e:
            return "ERROR: " + "\n".join(e.errors)
        except (ValueError, KeyError, TypeError, FileNotFoundError, RuntimeError, OSError) as e:
            return f"ERROR: {type(e).__name__}: {e}"
        except Exception as e:  # noqa: BLE001
            _log(f"reel_mcp: unexpected {type(e).__name__}: {e}")
            return f"ERROR: unexpected {type(e).__name__}: {e}"
    return wrapper


def _safe_async(fn):
    @functools.wraps(fn)
    async def wrapper(*a, **k):
        try:
            return await fn(*a, **k)
        except ReelError as e:
            return "ERROR: " + "\n".join(e.errors)
        except (ValueError, KeyError, TypeError, FileNotFoundError, RuntimeError, OSError) as e:
            return f"ERROR: {type(e).__name__}: {e}"
        except Exception as e:  # noqa: BLE001
            _log(f"reel_mcp: unexpected {type(e).__name__}: {e}")
            return f"ERROR: unexpected {type(e).__name__}: {e}"
    return wrapper


def _src(reel: Any, project: str | None = None):
    if project:
        if reel:
            raise ValueError("give either reel or project, not both")
        p = projmod.project_dir(project) / f"{project}.reel.json"
        if not p.is_file():
            raise ValueError(f"project {project!r} has no reel yet; call auto_reel first")
        return str(p)
    if reel is None or reel == "":
        raise ValueError("give a reel (JSON object or path) or project=<name>")
    if isinstance(reel, str):
        s = reel.strip()
        if s.startswith("{"):
            return json.loads(s)
        return s                                   # a path to a *.reel.json inside the allowed folders
    if not isinstance(reel, dict):
        raise ValueError("reel must be a JSON object (or a path to a .reel.json file)")
    return reel


def _plan(reel: Any, project: str | None = None) -> render.Plan:
    plan = render.prepare(_src(reel, project))
    if plan.spans[-1].end > MAX_SECONDS:
        raise ValueError(f"reel is {plan.spans[-1].end:.0f}s; the server limit is {MAX_SECONDS:.0f}s")
    return plan


# --------------------------------------------------------------------------- #
@server.tool()
@_safe
def copy_lines(path: str = "", project: str = "") -> str:
    """READ-ONLY. The copy deck: every copy ID with its exact on-screen text (and spoken text if set).
    Use these IDs in shots. With project=<name> it shows the project's APPROVED copy.json and its lock status
    (and, separately, any unapproved draft). There is no way to change the deck from here except propose_copy +
    the user's approval."""
    if project:
        d = projmod.project_dir(project)
        state = projmod.copy_lock_error(project)
        out = [f"project {project!r}: approved copy.json "
               + ("is LOCKED and intact" if state is None else f"is NOT usable: {state}")]
        if (d / "copy.json").is_file():
            out.append(approval.approved_listing(project))
        if (d / approval.DRAFT).is_file() and (not (d / "copy.json").is_file()
                                              or (d / approval.DRAFT).read_bytes() != (d / "copy.json").read_bytes()):
            _, deck = approval.read_draft(project)
            out.append("pending DRAFT (not approved, never rendered until approve_copy):\n" + approval.listing(deck))
        return "\n".join(out)
    cands = [path] if path else ["inputs/brand/copy.json", "copy.json"]
    last = None
    for c in cands:
        try:
            deck = copydeck.load_copy(resolve_path(c))
            break
        except (FileNotFoundError, ValueError) as e:
            last = e
    else:
        raise ValueError(f"no copy deck found ({last}); pass path= of a copy.json under {funk_home()} or {funk_inputs()}, "
                         f"or project=<name>")
    out = [f"copy deck: {deck.path}", f"brand_name: {deck.brand_name!r}"]
    for cid, ln in deck.lines.items():
        say = f"   say: {deck.say(cid)!r}" if ln.say is not None or deck.pronounce else ""
        out.append(f"  {cid}: {json.dumps(ln.text, ensure_ascii=False)}{say}")
    return "\n".join(out)


@server.tool()
@_safe
def list_templates() -> str:
    """Caption styles, end-card templates (with their copy slots), reveals, motions, transitions, anchors."""
    lines = ["caption styles: " + ", ".join(captions.list_styles()),
             "reveals: " + ", ".join(REVEALS), "anchors: " + ", ".join(ANCHORS),
             "motions: " + ", ".join(MOTION_TYPES) + "  (parallax needs the depth model, else Ken Burns)",
             "transitions: " + ", ".join(TRANSITIONS),
             "sources: image | video(trim, loop) | color(#hex) | funk2(recipe, must not draw text)",
             "durations: seconds | \"auto\" (voice-over length) | \"beats:N\""]
    for t in endcard.list_templates():
        lines.append(f"end card {t!r}: slots {endcard.load_template(t)['slots']}")
    return "\n".join(lines)


@server.tool()
@_safe
def validate(reel: dict | None = None, project: str = "") -> str:
    """Check a reel (or project=<name>): schema, no literal text, copy IDs, files, caption fit, caption/product
    collisions, timing, and for projects that copy.json is the approved, locked deck. Returns errors/warnings."""
    return on_gl(lambda: _validate(_src(reel, project or None)).text())


@server.tool()
@_safe
def storyboard(reel: dict | None = None, project: str = "") -> list:
    """Contact sheet: one frame per shot (at its midpoint) plus the end card, with shot id, duration and
    copy IDs printed below each frame. The fast way to iterate. Takes a reel or project=<name>."""
    def go():
        plan = _plan(reel, project or None)
        where = (projmod.project_dir(project) / "out" / f"{project}-storyboard.png") if project else funk_out() / "storyboard.png"
        return _storyboard(plan, where, tile_w=270), plan, where
    img, plan, where = on_gl(go)
    return [f"{len(plan.spans)} tiles, total {plan.spans[-1].end:.2f}s; saved {where}", _png(img, 1400)]


@server.tool()
@_safe
def still(reel: dict | None = None, shot_id: str = "", t: float = 0.5, project: str = "") -> list:
    """One frame of a shot, t seconds into it (use shot_id 'end_card' for the end card). Takes a reel or project=<name>."""
    if not shot_id:
        raise ValueError("shot_id is required (use 'end_card' for the end card)")
    def go():
        plan = _plan(reel, project or None)
        st = plan.reel.settings
        w = 540
        r = render.Renderer(plan.reel, plan.deck, plan.spans, width=w, height=round(w * st.height / st.width),
                            depth_mode="cached")
        try:
            return r.still(shot_id, t)
        finally:
            r.release()
    return [_png(PILImage.fromarray(on_gl(go)))]


def _render_result(out, audit: bool) -> list:
    lines = [f"{k}: {v}" for k, v in out.paths.items()] + [out.report_line()]
    lines += [f"warning: {w}" for w in out.warnings]
    if audit:
        lines.append(f"text audit: {'OK, every drawn string matches the copy deck' if out.audit_ok else 'PROBLEMS, see text_audit.json'}")
    res: list = ["\n".join(lines)]
    if "png" in out.paths:
        res.append(_png(PILImage.open(out.paths["png"])))
    return res


@server.tool(name="render")
@_safe
def render_tool(reel: dict | None = None, name: str = "", draft: bool = False, audit: bool = True,
                project: str = "") -> list:
    """Render the reel (or project=<name>) to <out>/<name>.mp4 (a project renders into its own out/ folder and
    name defaults to the project name). Use draft=true first (540x960, 24fps, no depth model).
    Also writes <name>.text_audit.json proving every drawn string equals its copy entry."""
    name = name or (project or "")
    if not OUT_NAME_RE.match(name or ""):
        raise ValueError("bad name: use 1-60 chars of A-Z a-z 0-9 _ -")
    out_dir = projmod.project_dir(project) / "out" if project else None
    def go():
        plan = _plan(reel, project or None)
        return render.render_reel(None, name, out_dir=out_dir, draft=draft, audit=audit, plan=plan)
    return _render_result(on_gl(go), audit)


# --------------------------------------------------------------------------- #
# Copy: draft -> approve -> lock  (the only way text gets into a project's copy.json)
# --------------------------------------------------------------------------- #
@server.tool()
@_safe
def propose_copy(project: str, lines: dict, pronounce: dict | None = None, brand_name: str | None = None) -> str:
    """Write a DRAFT of the ad copy: lines = {id: {"text": "...", "say": "optional spoken form"}} (id like hook,
    body1, end_head, url; text <= 90 chars, written in the final case, drawn EXACTLY as typed). Nothing here can
    reach the screen or the voice-over until the user approves it. The result is a numbered, exact listing:
    show it to the user VERBATIM and ask for approval, then call approve_copy with the draft_sha256 it gives."""
    res = approval.propose_copy(project, lines, pronounce, brand_name)
    return approval.propose_text(res, project)


def _client_elicits(ctx: Any) -> bool:
    try:
        from mcp import types
        return bool(ctx.session.check_client_capability(types.ClientCapabilities(elicitation=types.ElicitationCapability())))
    except Exception:  # noqa: BLE001
        return False


class CopyApproval(BaseModel):
    decision: Literal["approve", "reject"] = Field(description="Approve exactly this copy, or reject it")


def _elicit_unsupported(e: BaseException) -> bool:
    code = getattr(getattr(e, "error", None), "code", None)
    return code == -32601 or type(e).__name__ in ("NoBackChannelError",)


@server.tool()
@_safe_async
async def approve_copy(project: str, draft_sha256: str, ctx: Context, user_confirmed_in_chat: bool = False) -> str:
    """Approve the current draft copy and lock it as the project's copy.json. draft_sha256 must be the hash from
    the LATEST propose_copy (a stale hash is rejected). The user is asked to approve the exact listing in their
    client (a click you cannot make for them). Only if their client cannot show that prompt, pass
    user_confirmed_in_chat=true: only set this after the user explicitly approved the exact listing in this
    conversation. Never set it otherwise, and it never overrides a user's rejection."""
    lst, sha = approval.prepare_approval(project, draft_sha256)
    method = None
    if _client_elicits(ctx):
        msg = (f"Approve this exact copy for project {project!r}? Only approved lines can appear on screen or in the "
               f"voice-over.\n\n{lst}")
        try:
            res = await ctx.elicit(message=msg, schema=CopyApproval)
        except Exception as e:  # noqa: BLE001
            if not _elicit_unsupported(e):
                return f"NOT approved: the approval prompt failed ({type(e).__name__}: {e}). Nothing was changed."
        else:
            if res.action != "accept" or getattr(res.data, "decision", None) != "approve":
                return ("NOT approved: the user rejected or dismissed the listing. Ask what to change, call "
                        "propose_copy with the revised lines and show the new listing.")
            method = "elicitation"
    if method is None:
        if not user_confirmed_in_chat:
            return ("NOT approved: this client cannot show an approval prompt, so the user must approve in chat. "
                    "Show the listing below to the user verbatim and ask them to approve it. Only after they "
                    "explicitly approve exactly these lines, call approve_copy again with the same draft_sha256 and "
                    "user_confirmed_in_chat=true.\n\n" + lst)
        method = "chat"
    out = approval.approve(project, sha, method)
    return (f"APPROVED ({method}): {out['lines']} line(s) written to copy.json and locked "
            f"(sha256 {out['copy_lock'][:12]}...). Any later change to copy.json invalidates the lock.")


@server.tool()
@_safe
def copy_diff(project: str) -> str:
    """Line-by-line difference between the pending draft and the approved copy.json."""
    return approval.copy_diff(project)


# --------------------------------------------------------------------------- #
# Projects: drop files in, get an ad
# --------------------------------------------------------------------------- #
@server.tool()
@_safe
def new_project(name: str) -> str:
    """Start a project (a folder under projects/ with product/, brand/, shots/, clips/, music/). Then ask the user
    to drop their files in the inbox folder (or tell you a folder path) and call ingest."""
    meta = projmod.new_project(name)
    slug = meta["slug"]
    return (f"project {slug!r} created at {projmod.project_dir(slug)}\n"
            f"inbox (the user drops product photo, logo, music, clips and scene images here): {projmod.inbox_dir()}\n"
            f"next: when the files are in, call ingest(project={slug!r}); the user may also name any other folder.")


@server.tool()
@_safe
def list_projects() -> str:
    """List projects with their status (new, ingested, branded, copy-drafted, copy-approved, reel-built, rendered)."""
    ps = projmod.list_projects()
    if not ps:
        return f"no projects yet; call new_project. inbox: {projmod.inbox_dir()}"
    return "\n".join(f"{p['slug']}: {p['status']}; product {'yes' if p['has_product'] else 'no'}, copy "
                     f"{'approved' if p['copy_approved'] else 'not approved'}, {p['shots']} shots, {p['clips']} clips, "
                     f"{p['music']} music" for p in ps) + f"\ninbox: {projmod.inbox_dir()}"


@server.tool()
@_safe
def ingest(project: str, source: str = "", move: bool = True) -> list:
    """Classify and file what the user dropped: audio -> music/, video -> clips/, fonts and logos -> brand/, the
    product photo -> product/ (with an alpha cut-out and a quality score), other images -> shots/. source defaults
    to the inbox folder (files are moved); any other folder or file path the user names is only COPIED. Returns a
    manifest and a contact sheet. If the product is ambiguous it returns candidates: ask the user, then call
    set_product."""
    res = ingestmod.ingest(project, source or None, move)
    out: list = [res.text()]
    if res.contact_sheet:
        out.append(_png(PILImage.open(res.contact_sheet), 1200))
    return out


@server.tool()
@_safe
def set_product(project: str, file: str) -> str:
    """Choose which image is the product (a file name from the ingest candidates, or a project-relative path such
    as shots/x.jpg). Rebuilds product/product.png (real alpha kept, else a cut-out) and reports the cut-out quality."""
    pr = ingestmod.set_product(project, file)
    q = pr["cutout"]
    msg = (f"product set to {pr['file']} (original {pr['original']}); cut-out score {q['score']} "
           f"[{'OK' if q['ok'] else 'POOR'}], touches edge: {q['touches_edge']}, holes: {q['holes']}")
    return msg + "".join(f"\n  problem: {p}" for p in q["problems"]) + (f"\n  {q['advice']}" if q.get("advice") else "")


@server.tool()
@_safe
def brand_from_assets(project: str) -> list:
    """Derive the brand from the product cut-out and logo: primary (most saturated colour), secondary (darkest),
    text colour (white or #111111, WCAG contrast >= 4.5) and fonts (the user's, else the bundled ones). Returns a
    swatch image. Override any value with set_brand."""
    b = brandmod.brand_from_assets(project)
    return [brandmod.brand_text(b), _png(PILImage.open(b["swatch"]), 900)]


@server.tool()
@_safe
def set_brand(project: str, primary: str | None = None, secondary: str | None = None, text: str | None = None,
              font_caption: str | None = None, font_display: str | None = None) -> list:
    """Override brand values: colours as #rrggbb, fonts as paths to .ttf/.otf inside the project or the bundled fonts/."""
    b = brandmod.set_brand(project, primary, secondary, text, font_caption, font_display)
    return [brandmod.brand_text(b), _png(PILImage.open(b["swatch"]), 900)]


@server.tool()
@_safe
def auto_reel(project: str, length_s: float = 20.0, style: str = "red-box", pace: str = "fast",
              cta_ids: list[str] | None = None, music: str | None = None) -> list:
    """Build <project>.reel.json from the ingested assets and the APPROVED copy (it refuses an unapproved or
    modified copy.json): hook first, body lines, a branded end card (lines named end_* plus url), product in
    every other shot, clips at most 40% of shots, caption anchors that avoid the product, funk2 brand-colour
    filler when there are fewer than 4 visuals. Returns the reel JSON, the validate report and a storyboard."""
    def go():
        res = autoreel.auto_reel(project, length_s, style, pace, cta_ids, music)
        plan = render.prepare(str(res.path))
        res.storyboard = projmod.project_dir(project) / "out" / f"{project}-storyboard.png"
        img = _storyboard(plan, res.storyboard, tile_w=270)
        return res, img
    res, img = on_gl(go)
    return [res.text() + "\n\nreel JSON:\n" + json.dumps(res.reel, indent=2), _png(img, 1400)]


@server.tool()
@_safe
def project_render(project: str, draft: bool = True) -> list:
    """Render the project's reel into projects/<project>/out/ (draft=true: 540x960, 24 fps, quick). The text audit
    proves every drawn string equals the approved copy. Look at the poster, then render again with draft=false."""
    name = f"{project}-draft" if draft else project
    _src(None, project)                                 # fails early when the project has no reel yet
    def go():
        plan = _plan(None, project)
        return render.render_reel(None, name, out_dir=projmod.project_dir(project) / "out", draft=draft, audit=True,
                                  plan=plan)
    out = on_gl(go)
    projmod.advance_status(project, "rendered")
    return _render_result(out, True)


def main() -> None:
    projmod.ensure_inbox()
    projmod.projects_root().mkdir(parents=True, exist_ok=True)
    funk_inputs().mkdir(parents=True, exist_ok=True)
    funk_out().mkdir(parents=True, exist_ok=True)
    server.run()


if __name__ == "__main__":
    main()
