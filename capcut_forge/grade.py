"""Colour grades, in CapCut's own Adjust-panel units.

A grade is a dict of slider -> value, each -1..1 (CapCut's UI shows value*50,
so 0.2 = "10" on the slider). The CapCut backend writes these as real Adjust
materials on every clip, so the grade stays editable in the app; the ffmpeg
backend approximates the same sliders with eq/curves/colortemperature.
"""
from __future__ import annotations

SLIDERS = ("brightness", "contrast", "saturation", "light_sensation", "highlight",
           "shadow", "white", "black", "temperature", "tone", "sharpen", "vignetting")

PRESETS: dict[str, dict[str, float]] = {
    # Film look: rolled-off highlights, faded blacks, a little desaturated and
    # warm, strong vignette. Pair with `capcut-forge match` so every shot sits in it.
    "cinematic": {"contrast": 0.12, "saturation": -0.06, "highlight": -0.25,
                  "shadow": 0.08, "white": -0.08, "black": 0.12, "temperature": 0.05,
                  "sharpen": 0.05, "vignetting": 0.28},
    # Gentle and flattering: softened highlights, lifted shadows, a touch warm.
    "soft-cinematic": {"contrast": 0.08, "saturation": 0.08, "highlight": -0.22,
                       "shadow": 0.14, "white": -0.06, "black": 0.08,
                       "temperature": 0.08, "sharpen": 0.06, "vignetting": 0.18},
    # Golden-hour warmth with a faded film base.
    "warm-film": {"brightness": 0.03, "contrast": 0.05, "saturation": 0.04,
                  "highlight": -0.18, "shadow": 0.12, "black": 0.14,
                  "temperature": 0.22, "tone": 0.05, "vignetting": 0.22},
    # Cool, moody, contrasty.
    "moody-teal": {"brightness": -0.04, "contrast": 0.16, "saturation": -0.10,
                   "highlight": -0.15, "shadow": -0.06, "black": 0.06,
                   "temperature": -0.18, "tone": -0.06, "vignetting": 0.28},
    # Bright and clean for talking heads.
    "clean-bright": {"brightness": 0.06, "contrast": 0.06, "saturation": 0.10,
                     "highlight": -0.12, "shadow": 0.16, "white": 0.04,
                     "temperature": 0.04, "sharpen": 0.10},
}


def resolve(spec: str | dict | None, **overrides: float) -> dict[str, float]:
    """Preset name or dict, plus per-slider overrides -> validated grade."""
    if spec is None:
        base = {}
    elif isinstance(spec, str):
        if spec not in PRESETS:
            raise ValueError(f"unknown grade {spec!r} (have: {', '.join(PRESETS)})")
        base = dict(PRESETS[spec])
    else:
        base = dict(spec)
    base.update({k: v for k, v in overrides.items() if v is not None})
    bad = [k for k in base if k not in SLIDERS]
    if bad:
        raise ValueError(f"unknown slider(s) {bad} (have: {', '.join(SLIDERS)})")
    return {k: max(-1.0, min(1.0, float(v))) for k, v in base.items() if v}


def ffmpeg_filters(g: dict[str, float]) -> str:
    """Approximate the CapCut sliders with ffmpeg filters (render backend)."""
    if not g:
        return ""
    parts = []
    b, c, s = g.get("brightness", 0), g.get("contrast", 0), g.get("saturation", 0)
    if b or c or s:
        parts.append(f"eq=brightness={b * 0.25:.4f}:contrast={1 + c * 0.5:.4f}:"
                     f"saturation={1 + s:.4f}")
    # Tonal regions as one master curve: black point, shadows, highlights, white point.
    blk, sh = g.get("black", 0), g.get("shadow", 0) + g.get("light_sensation", 0) * 0.5
    hi, wh = g.get("highlight", 0), g.get("white", 0)
    if blk or sh or hi or wh:
        pts = [(0.0, max(0.0, blk * 0.12)), (0.25, 0.25 + sh * 0.10),
               (0.75, 0.75 + hi * 0.10), (1.0, min(1.0, 1 + wh * 0.12))]
        parts.append("curves=master='" + " ".join(f"{x:.3f}/{y:.3f}" for x, y in pts) + "'")
    t = g.get("temperature", 0)
    if t:
        parts.append(f"colortemperature=temperature={6500 - t * 2500:.0f}:mix={min(1, abs(t) * 1.5):.3f}")
    tone = g.get("tone", 0)
    if tone:   # CapCut "tone" is the green<->magenta tint
        parts.append(f"colorbalance=gm={-tone * 0.15:.4f}:gh={-tone * 0.1:.4f}")
    if g.get("sharpen", 0) > 0:
        parts.append(f"unsharp=5:5:{g['sharpen'] * 1.5:.3f}")
    if g.get("vignetting", 0) > 0:
        parts.append(f"vignette=angle={0.2 + g['vignetting'] * 0.5:.3f}")
    return ",".join(parts)
