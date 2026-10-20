"""MCP tools of the ``reel`` and ``project`` areas: shot list in, vertical video ad out; projects from dropped files.

Plain module-level functions (``reel_*``, ``project_*``) plus ``register(server)``.
Copy: ``project_propose_copy`` writes only copy.draft.json; copy.json is written solely by ``project_approve_copy``
after the user approves the exact listing (MCP elicitation click, or an explicit chat confirmation when the client
has none). No other tool writes copy.json. GL work runs on the one GL thread (``mcp_util.on_gl``).
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from PIL import Image as PILImage
from pydantic import BaseModel, Field

from funkbox.config import funk_home, funk_inputs, funk_out, resolve_path
from funkbox.mcp_util import Context, on_gl, png as _png, safe, safe_async
from funkbox.reel import approval, autoreel, brand as brandmod, captions, copydeck, endcard, ingest as ingestmod, render
from funkbox.reel import project as projmod
from funkbox.reel.schema import ANCHORS, MOTION_TYPES, REVEALS, TRANSITIONS
from funkbox.reel.storyboard import storyboard as _storyboard
from funkbox.reel.validate import validate as _validate

OUT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
MAX_SECONDS = 60.0


def _src(reel: Any, project: str | None = None):
    if project:
        if reel:
            raise ValueError("give either reel or project, not both")
        p = projmod.project_dir(project) / f"{project}.reel.json"
        if not p.is_file():
            raise ValueError(f"project {project!r} has no reel yet; call project_auto_reel first")
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
@safe
def reel_copy_lines(path: str = "", project: str = "") -> str:
    """READ-ONLY. The copy deck: every copy ID with its exact on-screen text (and spoken text if set).
    Use these IDs in shots. With project=<name> it shows the project's APPROVED copy.json and its lock status
    (and, separately, any unapproved draft). There is no way to change the deck from here except project_propose_copy +
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
            out.append("pending DRAFT (not approved, never rendered until project_approve_copy):\n" + approval.listing(deck))
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


@safe
def reel_templates() -> str:
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


@safe
def reel_validate(reel: dict | str | None = None, project: str = "") -> str:
    """Check a reel (or project=<name>): schema, no literal text, copy IDs, files, caption fit, caption/product
    collisions, timing, and for projects that copy.json is the approved, locked deck. Returns errors/warnings."""
    return on_gl(lambda: _validate(_src(reel, project or None)).text())


@safe
def reel_storyboard(reel: dict | str | None = None, project: str = "") -> list:
    """Contact sheet: one frame per shot (at its midpoint) plus the end card, with shot id, duration and
    copy IDs printed below each frame. The fast way to iterate. Takes a reel or project=<name>."""
    def go():
        plan = _plan(reel, project or None)
        where = (projmod.project_dir(project) / "out" / f"{project}-storyboard.png") if project else funk_out() / "storyboard.png"
        return _storyboard(plan, where, tile_w=270), plan, where
    img, plan, where = on_gl(go)
    return [f"{len(plan.spans)} tiles, total {plan.spans[-1].end:.2f}s; saved {where}", _png(img, 1400)]


@safe
def reel_still(reel: dict | str | None = None, shot_id: str = "", t: float = 0.5, project: str = "") -> list:
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


@safe
def reel_render(reel: dict | str | None = None, name: str = "", draft: bool = False, audit: bool = True,
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
@safe
def project_propose_copy(project: str, lines: dict, pronounce: dict | None = None, brand_name: str | None = None) -> str:
    """Write a DRAFT of the ad copy: lines = {id: {"text": "...", "say": "optional spoken form"}} (id like hook,
    body1, end_head, url; text <= 90 chars, written in the final case, drawn EXACTLY as typed). Nothing here can
    reach the screen or the voice-over until the user approves it. The result is a numbered, exact listing:
    show it to the user VERBATIM and ask for approval, then call project_approve_copy with the draft_sha256 it gives."""
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


@safe_async
async def project_approve_copy(project: str, draft_sha256: str, ctx: Context, user_confirmed_in_chat: bool = False) -> str:
    """Approve the current draft copy and lock it as the project's copy.json. draft_sha256 must be the hash from
    the LATEST project_propose_copy (a stale hash is rejected). The user is asked to approve the exact listing in their
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
                        "project_propose_copy with the revised lines and show the new listing.")
            method = "elicitation"
    if method is None:
        if not user_confirmed_in_chat:
            return ("NOT approved: this client cannot show an approval prompt, so the user must approve in chat. "
                    "Show the listing below to the user verbatim and ask them to approve it. Only after they "
                    "explicitly approve exactly these lines, call project_approve_copy again with the same draft_sha256 and "
                    "user_confirmed_in_chat=true.\n\n" + lst)
        method = "chat"
    out = approval.approve(project, sha, method)
    return (f"APPROVED ({method}): {out['lines']} line(s) written to copy.json and locked "
            f"(sha256 {out['copy_lock'][:12]}...). Any later change to copy.json invalidates the lock.")


@safe
def project_copy_diff(project: str) -> str:
    """Line-by-line difference between the pending draft and the approved copy.json."""
    return approval.copy_diff(project)


# --------------------------------------------------------------------------- #
# Projects: drop files in, get an ad
# --------------------------------------------------------------------------- #
@safe
def project_new(name: str) -> str:
    """Start a project (a folder under projects/ with product/, brand/, shots/, clips/, music/). Then ask the user
    to drop their files in the inbox folder (or tell you a folder path) and call project_ingest."""
    meta = projmod.new_project(name)
    slug = meta["slug"]
    return (f"project {slug!r} created at {projmod.project_dir(slug)}\n"
            f"inbox (the user drops product photo, logo, music, clips and scene images here): {projmod.inbox_dir()}\n"
            f"next: when the files are in, call project_ingest(project={slug!r}); the user may also name any other folder.")


@safe
def project_list() -> str:
    """List projects with their status (new, ingested, branded, copy-drafted, copy-approved, reel-built, rendered)."""
    ps = projmod.list_projects()
    if not ps:
        return f"no projects yet; call new_project. inbox: {projmod.inbox_dir()}"
    return "\n".join(f"{p['slug']}: {p['status']}; product {'yes' if p['has_product'] else 'no'}, copy "
                     f"{'approved' if p['copy_approved'] else 'not approved'}, {p['shots']} shots, {p['clips']} clips, "
                     f"{p['music']} music" for p in ps) + f"\ninbox: {projmod.inbox_dir()}"


@safe
def project_ingest(project: str, source: str = "", move: bool = True) -> list:
    """Classify and file what the user dropped: audio -> music/, video -> clips/, fonts and logos -> brand/, the
    product photo -> product/ (with an alpha cut-out and a quality score), other images -> shots/. source defaults
    to the inbox folder (files are moved); any other folder or file path the user names is only COPIED. Returns a
    manifest and a contact sheet. If the product is ambiguous it returns candidates: ask the user, then call
    project_set_product."""
    res = ingestmod.ingest(project, source or None, move)
    out: list = [res.text()]
    if res.contact_sheet:
        out.append(_png(PILImage.open(res.contact_sheet), 1200))
    return out


@safe
def project_set_product(project: str, file: str) -> str:
    """Choose which image is the product (a file name from the ingest candidates, or a project-relative path such
    as shots/x.jpg). Rebuilds product/product.png (real alpha kept, else a cut-out) and reports the cut-out quality."""
    pr = ingestmod.set_product(project, file)
    q = pr["cutout"]
    msg = (f"product set to {pr['file']} (original {pr['original']}); cut-out score {q['score']} "
           f"[{'OK' if q['ok'] else 'POOR'}], touches edge: {q['touches_edge']}, holes: {q['holes']}")
    return msg + "".join(f"\n  problem: {p}" for p in q["problems"]) + (f"\n  {q['advice']}" if q.get("advice") else "")


@safe
def project_brand(project: str) -> list:
    """Derive the brand from the product cut-out and logo: primary (most saturated colour), secondary (darkest),
    text colour (white or #111111, WCAG contrast >= 4.5) and fonts (the user's, else the bundled ones). Returns a
    swatch image. Override any value with project_set_brand."""
    b = brandmod.brand_from_assets(project)
    return [brandmod.brand_text(b), _png(PILImage.open(b["swatch"]), 900)]


@safe
def project_set_brand(project: str, primary: str | None = None, secondary: str | None = None, text: str | None = None,
              font_caption: str | None = None, font_display: str | None = None) -> list:
    """Override brand values: colours as #rrggbb, fonts as paths to .ttf/.otf inside the project or the bundled fonts/."""
    b = brandmod.set_brand(project, primary, secondary, text, font_caption, font_display)
    return [brandmod.brand_text(b), _png(PILImage.open(b["swatch"]), 900)]


@safe
def project_auto_reel(project: str, length_s: float = 20.0, style: str = "red-box", pace: str = "fast",
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


@safe
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


#: every tool of the two areas; the copy lock test checks this list: no tool but project_approve_copy writes copy.json
TOOLS = [reel_copy_lines, reel_templates, reel_validate, reel_storyboard, reel_still, reel_render,
         project_new, project_list, project_ingest, project_set_product, project_brand, project_set_brand,
         project_propose_copy, project_approve_copy, project_copy_diff, project_auto_reel, project_render]


def register(server) -> None:
    projmod.ensure_inbox()
    for fn in TOOLS:
        server.tool(name=fn.__name__)(fn)
