"""GL context factory. One cached context per process."""
from __future__ import annotations

import os

import moderngl


class GLUnavailable(RuntimeError):
    """No OpenGL context could be created (carries both backends' error messages)."""


_CTX: moderngl.Context | None = None


def create_context(prefer: str | None = None) -> moderngl.Context:
    """Return the process-wide standalone GL 3.3 context, creating it on first use.

    Order: ``prefer`` / env ``FUNK_GL_BACKEND`` ("egl" or "default") if set, otherwise the
    default backend, then EGL as a fallback. Raises GLUnavailable if nothing works.
    """
    global _CTX
    if _CTX is not None:
        return _CTX
    choice = (prefer or os.environ.get("FUNK_GL_BACKEND") or "").strip().lower()
    errors: list[str] = []
    if choice in ("egl",):
        attempts = [("egl", dict(backend="egl"))]
    elif choice in ("default",):
        attempts = [("default", {})]
    elif choice == "":
        attempts = [("default", {}), ("egl", dict(backend="egl"))]
    else:
        raise GLUnavailable(f"unknown FUNK_GL_BACKEND {choice!r}; use 'egl' or 'default'")
    for label, kw in attempts:
        try:
            _CTX = moderngl.create_standalone_context(require=330, **kw)
            return _CTX
        except Exception as e:  # noqa: BLE001 - driver errors come in many types
            errors.append(f"{label}: {e}")
    raise GLUnavailable("could not create an OpenGL 3.3 context; " + " | ".join(errors))


def gl_info() -> dict:
    ctx = create_context()
    info = ctx.info
    return {"GL_RENDERER": info.get("GL_RENDERER", ""), "GL_VERSION": info.get("GL_VERSION", "")}
