"""MCP plumbing shared by every area: the import shim, tool wrappers and the one GL thread.

NOTHING may be printed to stdout in a server process: stdout is the MCP protocol. Logs go to stderr.
Importing this module needs the ``mcp`` package; the CLI never imports it.
"""
from __future__ import annotations

import functools
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from PIL import Image as PILImage

try:  # mcp >= 2
    from mcp.server.mcpserver import Context, Image, MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context, FastMCP as Server, Image  # type: ignore  # noqa: F401  (mcp 1.x names)


def log(*a: Any) -> None:
    print(*a, file=sys.stderr)


def png(img: PILImage.Image, max_side: int = 900) -> Image:
    """A PIL image as an MCP image block (PNG, longest side <= max_side; RGBA is flattened on a checkerboard)."""
    img = img.copy()
    if img.mode == "RGBA":
        from funkbox.stills.edit import checker_flatten
        img = checker_flatten(img)
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return Image(data=buf.getvalue(), format="png")


def _error_text(e: BaseException) -> str | None:
    """A readable one-liner for the errors tools are expected to raise; None for anything unexpected."""
    from funkbox.gl.context import GLUnavailable
    from funkbox.gl.shader import ShaderError
    from funkbox.reel.schema import ReelError
    from funkbox.stills.schema import PosterError
    if isinstance(e, GLUnavailable):
        return f"ERROR: no OpenGL context available. {e}"
    if isinstance(e, (ReelError, PosterError)):
        return "ERROR: " + "\n".join(e.errors)
    if isinstance(e, ShaderError):
        return "ERROR: " + str(e)
    if isinstance(e, (ValueError, KeyError, TypeError, FileNotFoundError, RuntimeError, OSError)):
        return f"ERROR: {type(e).__name__}: {e}"
    return None


def _handle(fn: Callable, e: Exception) -> str:
    text = _error_text(e)
    if text is None:
        log(f"funkbox: {fn.__name__} failed: unexpected {type(e).__name__}: {e}")
        text = f"ERROR: unexpected {type(e).__name__}: {e}"
    return text


def safe(fn: Callable) -> Callable:
    """Tools never raise: they return a readable ``ERROR: ...`` string instead of a traceback."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            return _handle(fn, e)
    return wrapper


def safe_async(fn: Callable) -> Callable:
    @functools.wraps(fn)
    async def wrapper(*a, **k):
        try:
            return await fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            return _handle(fn, e)
    return wrapper


# --------------------------------------------------------------------------- #
# GL contexts are thread-affine and the MCP runtime calls sync tools from a pool of worker threads,
# so ALL GL work (every area) runs on this one dedicated thread, against the one shared context.
# --------------------------------------------------------------------------- #
_GL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="funk-gl")


def on_gl(fn: Callable, *a, **k):
    return _GL.submit(lambda: fn(*a, **k)).result()
