"""Reel variants: expand one reel into N variant reels, each validated before any render.

``vary`` maps a field to a list of values; keys are zipped (equal lengths, at most ``MAX_VARIANTS``). Only the
fields the spec allows may vary, and nothing that reaches the copy deck. The input reel is never changed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..stills.variations import MAX_VARIANTS
from .validate import validate

VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v")
SOURCE_KINDS = ("image", "video", "color", "funk2", "grid")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_PALETTE = re.compile(r"^\s*(#[0-9a-fA-F]{6})\s*,\s*(#[0-9a-fA-F]{6})\s*$")
_ALLOWED = (
    "shots.<id>.source, shots.<id>.motion, brand.palette, audio.music, captions.<shot>.copy_id"
)


class ReelVariantError(ValueError):
    """One message per problem; a run with any problem renders nothing."""


def variant_hash(reel: dict) -> str:
    """sha256 of the canonical JSON of a reel: equal reels give equal hashes."""
    blob = json.dumps(reel, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def expand_reel(
    reel: dict,
    vary: dict,
    base_dir: str | Path | None = None,
    *,
    deep: bool = True,
) -> tuple[list[dict], list[str]]:
    """Return ``(variant reels, labels)`` for ``vary``. Raise ``ReelVariantError`` listing every problem."""
    if not isinstance(reel, dict):
        raise ReelVariantError(["reel must be a JSON object"])
    if not isinstance(vary, dict) or not vary:
        raise ReelVariantError(['vary must be an object like {"brand.palette": ["#111111,#222222", ...]}'])

    errors = [e for key in vary for e in _key_errors(key)]
    lengths: set[int] = set()
    for key, values in vary.items():
        if isinstance(values, list) and values:
            lengths.add(len(values))
        else:
            errors.append(f"vary[{key!r}] must be a non-empty list of values")
    if len(lengths) > 1:
        errors.append(
            f"vary lists must all have the same length (got {sorted(lengths)}); each position is one variant"
        )
    if errors:
        raise ReelVariantError(errors)

    n = lengths.pop()
    if n > MAX_VARIANTS:
        raise ReelVariantError([f"at most {MAX_VARIANTS} variants (got {n})"])

    variants: list[dict] = []
    labels: list[str] = []
    errors = []
    for i in range(n):
        variant = copy.deepcopy(reel)
        try:
            for key, values in vary.items():
                _apply(variant, key, values[i])
        except ReelVariantError as e:
            errors.append(f"variant {i + 1}: {e}")
            continue
        report = validate(variant, base_dir=base_dir, deep=deep)
        errors += [f"variant {i + 1}: {e}" for e in report.errors]
        variants.append(variant)
        labels.append(f"{i + 1}: " + ", ".join(f"{k}={vary[k][i]}" for k in vary))
    if errors:
        raise ReelVariantError(errors)
    return variants, labels


def _key_errors(key: str) -> list[str]:
    parts = key.split(".")
    if parts[-1] == "text":
        return [f"vary key {key!r}: literal text cannot vary; copy comes from the approved deck"]
    if _is_caption_copy_id(parts):
        return []
    named = [p for i, p in enumerate(parts) if not (parts[0] == "shots" and i == 1)]  # the shot id is not a field
    if any(p == "copy" or p.startswith("copy_") for p in named):
        return [f"vary key {key!r}: the copy deck cannot vary; use captions.<shot>.copy_id"]
    if key in ("brand.palette", "audio.music"):
        return []
    if parts[0] == "shots" and len(parts) == 3 and parts[2] in ("source", "motion"):
        return []
    return [f"vary key {key!r}: not a variable field (allowed: {_ALLOWED})"]


def _is_caption_copy_id(parts: list[str]) -> bool:
    return len(parts) == 3 and parts[0] == "captions" and parts[2] == "copy_id"


def _apply(variant: dict, key: str, value: Any) -> None:
    """Set one vary value on a variant (a deep copy, so mutating it is safe). Raise ``ReelVariantError``."""
    parts = key.split(".")
    if key == "brand.palette":
        match = _PALETTE.match(value) if isinstance(value, str) else None
        if match is None:
            raise ReelVariantError(f"brand.palette needs 'primary,secondary' as two #rrggbb colours, got {value!r}")
        brand = variant.setdefault("brand", {})
        brand["primary"], brand["secondary"] = match.group(1), match.group(2)
    elif key == "audio.music":
        _safe_path(key, value)
        variant.setdefault("audio", {})["music"] = value
    elif parts[0] == "captions":
        caption = _shot(variant, parts[1]).get("caption")
        if not isinstance(caption, dict):
            raise ReelVariantError(f"shot {parts[1]!r} has no caption to set copy_id on")
        if not isinstance(value, str):
            raise ReelVariantError(f"{key}: copy_id must be a copy id string, got {value!r}")
        caption["copy_id"] = value
    elif parts[2] == "source":
        shot = _shot(variant, parts[1])
        shot["source"] = _source(key, shot.get("source"), value)
    else:  # shots.<id>.motion
        shot = _shot(variant, parts[1])
        shot["motion"] = _motion(key, shot.get("motion"), value)


def _shot(variant: dict, shot_id: str) -> dict:
    for shot in variant.get("shots", []):
        if isinstance(shot, dict) and shot.get("id") == shot_id:
            return shot
    raise ReelVariantError(f"no shot with id {shot_id!r}")


def _source(key: str, old: dict | None, value: Any) -> dict:
    """A path (image or video by extension), a #hex colour, or a whole source object."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ReelVariantError(f"{key}: expected a path, a #hex colour or a source object, got {value!r}")
    if _HEX.match(value):
        kind = "color"
    else:
        _safe_path(key, value)
        kind = "video" if value.lower().endswith(VIDEO_EXTS) else "image"
    # Keep the settings that are not the source itself (trim, fit, pad, speed).
    kept = {k: v for k, v in (old or {}).items() if k not in SOURCE_KINDS}
    kept[kind] = value
    return kept


def _motion(key: str, old: dict | None, value: Any) -> dict:
    """A motion type name (other motion settings are kept) or a whole motion object."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ReelVariantError(f"{key}: expected a motion type name or a motion object, got {value!r}")
    kept = dict(old) if isinstance(old, dict) else {}
    kept["type"] = value
    return kept


def _safe_path(key: str, value: Any) -> None:
    """Paths must stay inside the project: relative, with no '..' and no drive letter."""
    if not isinstance(value, str):
        raise ReelVariantError(f"{key}: expected a path, got {value!r}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", value) or value.startswith(("/", "\\")):
        raise ReelVariantError(f"{key}: {value!r} is outside the project")