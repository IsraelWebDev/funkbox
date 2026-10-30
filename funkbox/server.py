"""The one MCP server, ``funkbox`` (``python -m funkbox serve``, stdio).

Each area module exposes ``register(server)``; this file builds the server, registers them and runs it.
Tool names are prefixed by area: loop_*, reel_*, project_*, poster_*, image_*, apps_*.
NOTHING may be printed to stdout in a server process: stdout is the MCP protocol. Logs go to stderr.
"""
from __future__ import annotations

from funkbox import config
from funkbox.mcp_util import Server

INSTRUCTIONS = (
    "funkbox makes seamless shader loops (loop_*), vertical video ads from a shot list or from a project of dropped "
    "files (reel_*, project_*), layered posters and photo edits (poster_*, image_edit) and AI images through FastSD "
    "(image_*); apps_* starts and stops FastSD and reports on GIMP. "
    "Preview before you render (loop_preview and loop_sheet, reel_storyboard and reel_still, project_render with "
    "draft=true, poster_preview) and look at every picture. "
    "LOOP CONTRACT: shaders animate only through iPhase multiplied by an integer (or loopNoise); iTime or "
    "non-integer multiples break the loop, and loop_write_shader and loop_render will say so. "
    "On-screen and spoken text come only from the approved copy deck by id: never invent copy, and if a line is "
    "missing, ask the user to add it to copy.json, which only project_propose_copy (show its listing to the user "
    "verbatim) followed by the user's approval through project_approve_copy can change. "
    "The product image is composited, never generated, and literal poster text is allowed only in a poster marked "
    "casual. "
    "SD-Turbo is trained at 512x512, so ask image_generate for 'square' and let reels and posters cover-crop it, and "
    "never ask the model for a product or for text. "
    "GIMP is opened by the user (the 'GIMP + MCP' desktop shortcut), never by you: apps_start('gimp') tells you what to ask. "
    "Errors come back as text starting with ERROR:, and the loop, copy and path checks are there to be obeyed."
)

AREAS = ("funkbox.loops.tools", "funkbox.reel.tools", "funkbox.stills.tools", "funkbox.studio.tools",
         "funkbox.music.tools")


def build_server() -> Server:
    import importlib
    server = Server("funkbox", instructions=INSTRUCTIONS)
    for mod in AREAS:
        importlib.import_module(mod).register(server)
    return server


def serve() -> None:
    cfg = config.get()
    for d in (cfg.inputs, cfg.out, cfg.projects, cfg.inbox):
        d.mkdir(parents=True, exist_ok=True)
    build_server().run()
