"""Original ambient beds, synthesised locally with ffmpeg — royalty-free by
construction, nothing downloaded. A slowly breathing chord pad (each voice
detuned left/right for width) over soft filtered noise, with echo for space."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import media

# mood -> (chord in Hz, pad brightness Hz, noise colour, noise level)
MOODS: dict[str, tuple[list[float], int, str, float]] = {
    "warm":  ([110.00, 164.81, 220.00, 277.18, 329.63, 493.88], 2200, "brown", 0.05),
    "dark":  ([73.42, 110.00, 146.83, 174.61, 220.00, 261.63], 1400, "brown", 0.06),
    "airy":  ([261.63, 329.63, 392.00, 493.88, 587.33, 783.99], 4500, "pink", 0.035),
    "dreamy": ([138.59, 207.65, 277.18, 329.63, 415.30, 554.37], 3000, "pink", 0.04),
}


def _voice(freq: float, k: int, detune: float) -> str:
    """One pad voice: a sine whose loudness swells on its own slow cycle, so
    the chord keeps shifting colour instead of droning."""
    lfo = 0.03 + 0.017 * k
    return (f"sin(2*PI*{freq * detune:.3f}*t)"
            f"*(0.55+0.45*sin(2*PI*{lfo:.3f}*t+{k * 1.3:.2f}))")


def generate(out: str | Path, duration: float, mood: str = "warm",
             fade: float = 3.0) -> Path:
    if mood not in MOODS:
        raise ValueError(f"unknown mood {mood!r} (have: {', '.join(MOODS)})")
    chord, bright, noise, noise_amp = MOODS[mood]
    gain = 0.5 / len(chord)
    left = "+".join(_voice(f, k, 1.0 - 0.0015 * (k % 2)) for k, f in enumerate(chord))
    right = "+".join(_voice(f, k, 1.0 + 0.0015 * (k % 2 + 1)) for k, f in enumerate(chord))
    out = Path(out).expanduser().resolve()
    fade = min(fade, duration / 3)
    graph = (
        f"aevalsrc='{gain}*({left})|{gain}*({right})':s=48000:d={duration:.3f}[pad];"
        f"anoisesrc=c={noise}:a={noise_amp}:r=48000:d={duration:.3f},"
        f"lowpass=f=900,aformat=channel_layouts=stereo[air];"
        f"[pad]lowpass=f={bright},chorus=0.6:0.9:55:0.4:0.25:2[wide];"
        f"[wide][air]amix=inputs=2:normalize=0,"
        f"aecho=0.8:0.85:240|420:0.35|0.25,highpass=f=40,"
        f"afade=t=in:d={fade:.2f},afade=t=out:st={duration - fade:.3f}:d={fade:.2f},"
        f"alimiter=limit=0.7[out]"
    )
    subprocess.run([media.require("ffmpeg"), "-v", "error", "-y", "-filter_complex", graph,
                    "-map", "[out]", "-t", f"{duration:.3f}", "-c:a", "pcm_s16le", str(out)],
                   check=True)
    return out
