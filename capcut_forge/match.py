"""Shot matching: measure every clip, then give each one a per-shot grade
offset that pulls its exposure, colour cast and saturation toward the group,
so one look (the timeline grade) reads the same across all of them.

Measurements come from ffmpeg's signalstats over frames sampled from the kept
span only. Corrections are partial on purpose (`strength`): fully lifting a
night shot to daylight exposure just shows noise and kills the mood.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from . import media
from .timeline import Clip, Timeline


@dataclass
class ShotStats:
    luma: float        # mean Y, 0-255
    cast: float        # (V - U): + = warm/red, - = cool/blue
    tint: float        # green(-) / magenta(+) balance proxy
    sat: float         # mean saturation


def measure(clip: Clip, samples_per_sec: float = 2.0) -> ShotStats:
    if media.kind(clip.source) == "image":         # a still has one frame to look at
        span, rate = ["-i", clip.source], ""
    else:
        span = ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", clip.source]
        rate = f"fps={samples_per_sec},"
    out = subprocess.run(
        [media.require("ffmpeg"), "-v", "error", *span,
         "-vf", f"{rate}scale=480:-2,format=yuv444p,signalstats,metadata=print:file=-",
         "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    acc: dict[str, list[float]] = {}
    for line in out.splitlines():
        if "lavfi.signalstats." in line:
            k, v = line.split("lavfi.signalstats.")[1].split("=")
            acc.setdefault(k, []).append(float(v))
    if not acc:
        raise media.MediaError(f"could not measure {clip.source}")
    m = {k: sum(v) / len(v) for k, v in acc.items()}
    u, v = m["UAVG"] - 128, m["VAVG"] - 128
    return ShotStats(luma=m["YAVG"], cast=v - u, tint=-(u + v) / 2, sat=m["SATAVG"])


def _clamp(x: float, lim: float) -> float:
    return max(-lim, min(lim, x))


def offsets(stats: list[ShotStats], strength: float = 0.75,
            target_luma: float | None = None) -> list[dict[str, float]]:
    """Per-shot slider offsets toward the group (or a fixed target exposure)."""
    n = len(stats)
    tl = target_luma if target_luma is not None else sum(s.luma for s in stats) / n
    tc = sum(s.cast for s in stats) / n * 0.3      # keep a hint of the group's warmth
    ts = sum(s.sat for s in stats) / n
    out = []
    for s in stats:
        dy = (tl - s.luma) / 255 * strength
        o = {
            "brightness": _clamp(dy * 1.2, 0.30),
            # dark shots need their shadows opened, bright ones their highlights held
            "shadow": _clamp(dy * 1.4, 0.35) if dy > 0 else 0.0,
            "highlight": _clamp(dy * 1.0, 0.25) if dy < 0 else 0.0,
            "temperature": _clamp(-(s.cast - tc) * 0.025 * strength, 0.35),
            "tone": _clamp(-s.tint * 0.02 * strength, 0.2),
            "saturation": _clamp((ts - s.sat) / max(ts, 1e-3) * 0.4 * strength, 0.25),
        }
        out.append({k: round(v, 4) for k, v in o.items() if abs(v) >= 0.005})
    return out


def match(tl: Timeline, strength: float = 0.75, target_luma: float | None = None,
          log=print) -> list[ShotStats]:
    """Measure every clip of a timeline and write its matching offsets in place."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:      # one ffmpeg per shot
        stats = list(pool.map(measure, tl.clips))
    for c, s, o in zip(tl.clips, stats, offsets(stats, strength, target_luma)):
        c.grade = o
        log(f"{(c.note or c.source.rsplit('/', 1)[-1])[:34]:34} luma {s.luma:5.1f} "
            f"cast {s.cast:+5.1f} sat {s.sat:4.1f} -> "
            + ", ".join(f"{k} {v * 50:+.0f}" for k, v in o.items()))
    return stats
