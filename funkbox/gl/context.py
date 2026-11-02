"""GL context factory. One cached context per process."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import moderngl

from funkbox.config import get as _config
from funkbox.gl import hints


class GLUnavailable(RuntimeError):
    """No OpenGL context could be created (carries every backend's error message)."""


#: environment for Mesa's software rasteriser (llvmpipe)
SOFTWARE_ENV = {"LIBGL_ALWAYS_SOFTWARE": "1", "GALLIUM_DRIVER": "llvmpipe"}
#: True once a context was created through a software attempt or with a software renderer
SOFTWARE = False

_CTX: moderngl.Context | None = None
_NOTICE_SHOWN = False


def _all_attempts() -> list[tuple[str, dict, dict | None]]:
    """(label, kwargs, extra environment) for every context we know how to make."""
    return [
        ("default", {}, None),
        ("egl", {"backend": "egl"}, None),
        ("egl-software", {"backend": "egl"}, {**SOFTWARE_ENV, "EGL_PLATFORM": "surfaceless"}),
        ("default-software", {}, dict(SOFTWARE_ENV)),
    ]


def _attempts(choice: str) -> list[tuple[str, dict, dict | None]]:
    if choice == "":
        labels = ("default", "egl", "egl-software", "default-software")
    elif choice == "egl":
        labels = ("egl", "egl-software")
    elif choice == "default":
        labels = ("default", "default-software")
    elif choice == "software":
        labels = ("egl-software", "default-software")
    else:
        raise GLUnavailable(
            f"unknown FUNK_GL_BACKEND {choice!r}; use 'egl', 'default' or 'software'")
    return [a for a in _all_attempts() if a[0] in labels]


def _create(kwargs: dict, env: dict | None) -> moderngl.Context:
    """Create one context, applying ``env`` only for the call (restored even on error)."""
    saved: dict[str, str | None] = {}
    try:
        for key, value in (env or {}).items():
            saved[key] = os.environ.get(key)
            os.environ[key] = value
        return moderngl.create_standalone_context(require=330, **kwargs)
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _is_software_renderer(renderer: str) -> bool:
    low = renderer.lower()
    return any(word in low for word in ("llvmpipe", "softpipe", "software"))


def create_context(prefer: str | None = None) -> moderngl.Context:
    """Return the process-wide standalone GL 3.3 context, creating it on first use.

    ``prefer`` / ``FUNK_GL_BACKEND`` selects the order: "egl", "default", "software", or "" (try
    the hardware paths, then software OpenGL through Mesa). Software attempts set
    ``LIBGL_ALWAYS_SOFTWARE=1`` and ``GALLIUM_DRIVER=llvmpipe`` only for the duration of the
    attempt. Raises GLUnavailable, with an install hint on Linux, if nothing works.
    """
    global _CTX, SOFTWARE, _NOTICE_SHOWN
    if _CTX is not None:
        return _CTX
    choice = (prefer or _config().gl_backend or "").strip().lower()
    errors: list[str] = []
    for label, kwargs, env in _attempts(choice):
        try:
            ctx = _create(kwargs, env)
        except Exception as e:  # noqa: BLE001 - driver errors come in many types
            errors.append(f"{label}: {e}")
            continue
        renderer = str(ctx.info.get("GL_RENDERER", ""))
        software = label.endswith("-software")
        if software or _is_software_renderer(renderer):
            SOFTWARE = True
        if software and not _NOTICE_SHOWN:
            print("funkbox: no GPU OpenGL available; using software OpenGL (slower)",
                  file=sys.stderr)
            _NOTICE_SHOWN = True
        _CTX = ctx
        return _CTX
    message = "could not create an OpenGL 3.3 context; " + " | ".join(errors)
    hint = hints.install_hint(errors)
    if hint:
        message += "\n" + hint
    raise GLUnavailable(message)


@dataclass
class Probe:
    label: str
    ok: bool
    detail: str
    software: bool


def probe() -> list[Probe]:
    """Try every context variant independently, for diagnostics.

    Never touches the cached context and ignores FUNK_GL_BACKEND. Each context that is created is
    released immediately; ``detail`` is the renderer string on success or the error text on failure.
    """
    results: list[Probe] = []
    for label, kwargs, env in _all_attempts():
        is_software_attempt = label.endswith("-software")
        try:
            ctx = _create(kwargs, env)
        except Exception as e:  # noqa: BLE001
            results.append(Probe(label, False, str(e), is_software_attempt))
            continue
        try:
            renderer = str(ctx.info.get("GL_RENDERER", ""))
        except Exception:  # noqa: BLE001
            renderer = ""
        try:
            ctx.release()
        except Exception:  # noqa: BLE001
            pass
        results.append(Probe(label, True, renderer,
                             is_software_attempt or _is_software_renderer(renderer)))
    return results


def reset() -> None:
    """Forget the cached context and the software flags (tests)."""
    global _CTX, SOFTWARE, _NOTICE_SHOWN
    _CTX = None
    SOFTWARE = False
    _NOTICE_SHOWN = False


def gl_info() -> dict:
    ctx = create_context()
    info = ctx.info
    return {"GL_RENDERER": info.get("GL_RENDERER", ""), "GL_VERSION": info.get("GL_VERSION", "")}
