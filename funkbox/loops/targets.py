"""Delivery targets for ``funkbox loop render --target``: a named place a loop is going, with its size and length
rules, so a creator does not have to look the spec up.

``canvas`` is a Spotify Canvas (https://support.spotify.com/us/artists/article/canvas-guidelines/): a vertical 9:16
MP4, 3-8 seconds long, 720-1080 px. funkbox reads "720-1080 px" as the short side (720x1280 up to 1080x1920) and
renders 720x1280 by default. A loop is seamless already (the loop check still runs), so it repeats without a jump.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    name: str
    aspect: tuple[int, int]  # width : height
    short_side: tuple[int, int]  # allowed range of the shorter side, px
    seconds: tuple[float, float]  # allowed length range
    default_size: tuple[int, int]  # width, height
    spec: str


TARGETS = {
    "canvas": Target(
        name="canvas",
        aspect=(9, 16),
        short_side=(720, 1080),
        seconds=(3.0, 8.0),
        default_size=(720, 1280),
        spec="Spotify Canvas: 9:16, 720-1080 px, 3-8 s, MP4",
    )
}


def apply_target(settings, name: str, *, explicit_size: bool, explicit_seconds: bool, length_fixed: bool = False):
    """Set ``settings`` (a Settings2) for target ``name`` and check it; raises ValueError naming the rule broken.

    A size or length the user gave on purpose is checked, never changed. Otherwise the target's default size is
    used and the length is pulled into the target's window. ``length_fixed`` means something else sets the length
    (an audio loop or an audio track): it is checked but not moved."""
    t = TARGETS.get(name)
    if t is None:
        raise ValueError(f"unknown target {name!r}; have {sorted(TARGETS)}")
    if explicit_size:
        w, h = settings.width, settings.height
        if w * t.aspect[1] != h * t.aspect[0]:
            raise ValueError(f"{t.name}: {w}x{h} is not {t.aspect[0]}:{t.aspect[1]} ({t.spec})")
        short = min(w, h)
        if not (t.short_side[0] <= short <= t.short_side[1]):
            raise ValueError(
                f"{t.name}: {w}x{h} is outside {t.short_side[0]}-{t.short_side[1]} px on the short side ({t.spec})"
            )
    else:
        settings.width, settings.height = t.default_size
    lo, hi = t.seconds
    if explicit_seconds or length_fixed:
        if length_fixed and not explicit_seconds:
            return settings  # checked by the caller once the length is known
        if not (lo <= settings.seconds <= hi):
            raise ValueError(f"{t.name}: {settings.seconds:g} s is outside {lo:g}-{hi:g} s ({t.spec})")
    else:
        settings.seconds = min(max(settings.seconds, lo), hi)
    return settings


def check_seconds(seconds: float, name: str) -> None:
    """Raise ValueError when ``seconds`` is outside target ``name``'s length window."""
    t = TARGETS[name]
    lo, hi = t.seconds
    if not (lo <= seconds <= hi):
        raise ValueError(f"{t.name}: {seconds:g} s is outside {lo:g}-{hi:g} s ({t.spec})")
