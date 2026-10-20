"""Per-layer colour adjustments and filters, expressed as GPU passes (see gl.Step)."""
from __future__ import annotations

from .gl import PASSES, POST, Step

ADJUST_DEFAULTS = {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0, "hue": 0.0, "temperature": 0.0}


def adjust_values(a: dict) -> dict:
    """Uniform values (keyed u_<name>) for passes/adjust.frag from an `adjust` dict."""
    lv = a.get("levels", [0.0, 1.0, 1.0])
    return {"u_brightness": float(a.get("brightness", 0.0)), "u_contrast": float(a.get("contrast", 0.0)),
            "u_saturation": float(a.get("saturation", 0.0)), "u_hue": float(a.get("hue", 0.0)),
            "u_black": float(lv[0]), "u_white": float(lv[1]), "u_gamma": float(lv[2]),
            "u_temp": float(a.get("temperature", 0.0)), "u_invert": 1.0 if a.get("invert") else 0.0}


def _is_noop_adjust(a: dict | None) -> bool:
    if not a:
        return True
    return (all(abs(float(a.get(k, 0.0))) < 1e-9 for k in ADJUST_DEFAULTS)
            and list(a.get("levels", [0, 1, 1])) == [0, 1, 1] and not a.get("invert"))


def steps(adjust: dict | None, filters: dict | None, scale: float = 1.0) -> list[Step]:
    """The pass list for a layer: adjust, then filters in the fixed order blur, sharpen, posterize, halftone,
    duotone, pixelate, chroma, vignette. Pixel sizes are multiplied by ``scale`` (previews)."""
    out: list[Step] = []
    if not _is_noop_adjust(adjust):
        out.append(Step(PASSES / "adjust.frag", adjust_values(adjust)))
    f = filters or {}
    sigma = float(f.get("blur", 0.0)) * scale
    if sigma >= 0.3:
        taps = min(192, max(1, int(-(-3.0 * sigma // 1))))
        out.append(Step(PASSES / "blur.frag", {"u_sigma": sigma}, {"u_dir": (1, 0), "u_taps": taps}))
        out.append(Step(PASSES / "blur.frag", {"u_sigma": sigma}, {"u_dir": (0, 1), "u_taps": taps}))
    if float(f.get("sharpen", 0.0)) > 0:
        out.append(Step(PASSES / "sharpen.frag", {"u_amount": float(f["sharpen"])}))
    if int(f.get("posterize", 0)) >= 2:
        out.append(Step(POST / "posterize.frag", {"u_levels": float(f["posterize"])}, restore=True))
    if int(f.get("halftone", 0)) >= 3:
        out.append(Step(POST / "halftone.frag", {"u_cell": max(3.0, round(float(f["halftone"]) * scale))}, restore=True))
    if f.get("duotone"):
        out.append(Step(PASSES / "duotone.frag", {"u_dark": f["duotone"][0], "u_light": f["duotone"][1]}))
    if int(f.get("pixelate", 0)) >= 2:
        out.append(Step(PASSES / "pixelate.frag", {"u_cell": max(2.0, round(float(f["pixelate"]) * scale))}))
    if float(f.get("chroma", 0.0)) > 0:
        out.append(Step(POST / "chroma.frag", {"u_px": max(1.0, round(float(f["chroma"]) * scale))}, restore=True))
    if float(f.get("vignette", 0.0)) > 0:
        out.append(Step(POST / "vignette.frag", {"u_amount": float(f["vignette"])}, restore=True))
    return out
