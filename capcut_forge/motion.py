"""Camera-move presets -> clip keyframes.

CapCut's keyframe JSON supports custom bezier curves, but their control-point
encoding is undocumented; straight "Line" segments are not. So eased moves
are sampled: several Line keyframes along a smoothstep / ease-out curve,
which plays back as one smooth move.

Moves stay inside the frame: every move that shifts position also scales up
enough that no edge ever shows.
"""
from __future__ import annotations

from typing import Callable

Ease = Callable[[float], float]


def linear(u: float) -> float:
    return u


def smooth(u: float) -> float:            # ease-in-out (smootherstep)
    return u * u * u * (u * (u * 6 - 15) + 10)


def out_cubic(u: float) -> float:         # fast start, soft landing
    return 1 - (1 - u) ** 3


def in_cubic(u: float) -> float:
    return u ** 3


def ramp(a: float, b: float, t0: float, t1: float, ease: Ease = smooth,
         steps: int = 6) -> list[list[float]]:
    return [[round(t0 + (t1 - t0) * i / steps, 3), round(a + (b - a) * ease(i / steps), 4)]
            for i in range(steps + 1)]


def _end(d: float, fps: float) -> float:
    return round(d - 1 / fps, 3)          # last frame of the clip


def preset(name: str, d: float, fps: float = 25.0, k: float = 1.0) -> dict[str, list]:
    """Keyframes for a move over a clip of `d` seconds. `k` scales intensity."""
    e = _end(d, fps)
    s = lambda x: 1 + (x - 1) * k            # noqa: E731  intensity on scale amounts
    if name == "push-in":                    # slow dolly in
        return {"scale": ramp(1.0, s(1.12), 0, e, smooth)}
    if name == "pull-out":                   # reveal
        return {"scale": ramp(s(1.18), 1.02, 0, e, smooth)}
    if name == "impact":                     # snap in, then creep
        hit = min(0.45, d / 4)
        return {"scale": ramp(s(1.28), s(1.06), 0, hit, out_cubic, 4)[:-1]
                + ramp(s(1.06), s(1.12), hit, e, linear, 2)}
    if name in ("pan-left", "pan-right"):    # lateral drift on a slight zoom
        dx = 0.05 * k * (1 if name == "pan-right" else -1)
        return {"scale": [[0, s(1.14)], [e, s(1.14)]],
                "x": ramp(dx, -dx, 0, e, smooth)}
    if name == "drift":                      # diagonal float with a settling tilt
        return {"scale": ramp(s(1.16), s(1.10), 0, e, smooth),
                "x": ramp(0.035 * k, -0.035 * k, 0, e, smooth),
                "y": ramp(-0.02 * k, 0.02 * k, 0, e, smooth),
                "rotation": ramp(1.6 * k, 0.0, 0, e, smooth)}
    if name == "orbit":                      # slow arc: rotate + push, feels handheld-on-a-rig
        return {"scale": ramp(s(1.12), s(1.2), 0, e, smooth),
                "rotation": ramp(-1.8 * k, 1.8 * k, 0, e, smooth),
                "x": ramp(-0.02 * k, 0.02 * k, 0, e, smooth)}
    raise ValueError(f"unknown move {name!r} (have: {', '.join(MOVES)})")


MOVES = ("push-in", "pull-out", "impact", "pan-left", "pan-right", "drift", "orbit")


def fade(keys: dict[str, list], d: float, fps: float = 25.0, fade_in: float = 0.0,
         fade_out: float = 0.0) -> dict[str, list]:
    """Add opacity fades from/to black at the clip's edges."""
    pts = []
    if fade_in:
        pts += ramp(0.0, 1.0, 0, fade_in, smooth, 4)
    else:
        pts.append([0, 1.0])
    if fade_out:
        pts += ramp(1.0, 0.0, round(d - fade_out, 3), _end(d, fps), smooth, 4)
    else:
        pts.append([_end(d, fps), 1.0])
    out = dict(keys)
    out["opacity"] = [p for i, p in enumerate(pts) if i == 0 or p[0] > pts[i - 1][0]]
    return out

