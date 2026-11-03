"""Beat tracking and beat-cut montages.

The music is decoded with ffmpeg and analysed with numpy alone: a spectral-flux
onset envelope, tempo candidates from its autocorrelation, then the candidate
whose 8th-note grid catches the most onset peaks. Autocorrelation alone can't
tell 114 from 152 or 76 BPM in a syncopated groove (they share most multiples),
but only the true tempo puts the hits ON its grid. The grid is then fine-tuned
and kept fixed, which suits produced music with a steady tempo. Downbeats (bar
starts) are the beat phase with the most low-end energy, where the kick sits.

`montage` then lays clips end to end so every cut lands on a beat, with clip
lengths snapped to whole timeline frames so cuts can't drift as they add up.
"""
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import media
from .timeline import AudioTrack, Clip, Timeline

SR = 22050
HOP = 512                      # ~23 ms per onset frame
WIN = 2048
ONSET_LAG = 0.026              # seconds; measured on clicks, kicks and noise bursts


@dataclass
class Beats:
    bpm: float
    beats: list[float]         # seconds into the music
    downbeat: int              # index into `beats` of the first bar start
    per_bar: int = 4
    onsets_per_sec: float = 0.0   # under ~1.2 there is no beat to cut to (pads, ambience)

    @property
    def rhythmic(self) -> bool:
        return self.onsets_per_sec >= 1.2

    @property
    def bars(self) -> list[float]:
        return self.beats[self.downbeat::self.per_bar]


def _decode(path: str | Path) -> np.ndarray:
    out = subprocess.run(
        [media.require("ffmpeg"), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True)
    return np.frombuffer(out.stdout, dtype=np.float32)


def _envelopes(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(full-band onset strength, low-band onset strength), one value per hop."""
    n = 1 + max(0, len(y) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(y[idx] * np.hanning(WIN), axis=1))
    logspec = np.log1p(1000 * spec)
    flux = np.maximum(0.0, np.diff(logspec, axis=0, prepend=logspec[:1]))
    low_bins = int(150 / (SR / WIN))           # kick drum region, < 150 Hz
    env, low = flux.sum(axis=1), flux[:, :low_bins].sum(axis=1)
    k = np.ones(16) / 16                       # remove slow loudness swells
    env = np.maximum(0.0, env - np.convolve(env, k, mode="same"))
    return env / (env.max() or 1.0), low / (low.max() or 1.0)


def _candidates(env: np.ndarray, lo: float = 60.0, hi: float = 180.0,
                n: int = 6) -> list[float]:
    """Likely beat periods (onset frames): the strongest autocorrelation peaks."""
    fps = SR / HOP
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(int(fps * 60 / hi), int(fps * 60 / lo) + 1)
    peaks = [l for l in lags if ac[l] >= ac[l - 1] and ac[l] >= ac[l + 1] and ac[l] > 0]
    out = []
    for l in sorted(peaks, key=lambda l: -ac[l])[:n]:
        a, b, c = ac[l - 1], ac[l], ac[l + 1]   # parabolic refinement, sub-frame
        out.append(l + (0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0))
    return out


def _grid_fit(peaks: np.ndarray, weight: np.ndarray, period: float,
              step: float = 0.1) -> tuple[float, float]:
    """(share of onset-peak weight within 12% of an 8th note, best phase)."""
    best, phase = 0.0, 0.0
    for p in np.arange(0, period, step):
        pos = ((peaks - p) / period * 2) % 1
        share = weight[np.minimum(pos, 1 - pos) < 0.12].sum()
        if share > best:
            best, phase = share, p
    return best / weight.sum(), phase


_DETECTED: dict[tuple, Beats] = {}


def detect(path: str | Path, per_bar: int = 4) -> Beats:
    """Beat grid of a music file (cached per file version: it takes seconds)."""
    st = Path(path).stat()
    key = (str(Path(path).resolve()), st.st_size, st.st_mtime_ns, per_bar)
    if key not in _DETECTED:
        _DETECTED[key] = _detect(path, per_bar)
    return _DETECTED[key]


def _detect(path: str | Path, per_bar: int = 4) -> Beats:
    y = _decode(path)
    if len(y) < WIN * 4:
        raise media.MediaError(f"{path}: too short to find a beat")
    env, low = _envelopes(y)
    fps = SR / HOP

    pk = np.array([i for i in range(3, len(env) - 3)
                   if env[i] == env[i - 3:i + 4].max() and env[i] > env.mean() + env.std()])
    if len(pk) < 8:
        raise media.MediaError(f"{path}: no clear rhythm to follow")
    w = env[pk]

    def tune(P: float) -> float:
        # The fit is sharp: 0.5 BPM off drifts half a second over two minutes,
        # so refine each candidate (+-1.5%, then finer) before comparing them.
        P = max(np.linspace(P * 0.985, P * 1.015, 61), key=lambda q: _grid_fit(pk, w, q, 0.25)[0])
        return max(np.linspace(P * 0.9995, P * 1.0005, 21), key=lambda q: _grid_fit(pk, w, q)[0])

    period = max((tune(P) for P in _candidates(env)), key=lambda P: _grid_fit(pk, w, P)[0])
    phase = _grid_fit(pk, w, period)[1]
    # The fit above is a count inside a tolerance, so it's flat near the
    # answer. Least squares of the on-grid hits against their 8th-note index
    # pins the period exactly (no drift over a long track).
    for _ in range(3):
        half = period / 2
        idx = np.round((pk - phase) / half)
        near = np.abs(pk - phase - idx * half) < 0.12 * half
        if near.sum() < 8:
            break
        slope, phase = np.polyfit(idx[near], pk[near], 1, w=np.sqrt(w[near]))
        period = 2 * slope
    phase %= period
    # the 8th-note fit can't tell the beat from the off-beat: take the stronger
    grid = np.arange(phase, len(env) - 1, period)
    off = grid[:-1] + period / 2
    if env[np.round(off).astype(int)].sum() > env[np.round(grid).astype(int)].sum():
        grid = np.arange(phase + period / 2, len(env) - 1, period)

    # Flux peaks while a hit is still entering the window, so the frame centre
    # (WIN/2 after its start) runs ONSET_LAG ahead of the sound itself.
    beats = [round(float(f * HOP + WIN / 2) / SR + ONSET_LAG, 4) for f in grid]
    lows = [low[int(round(f))] for f in grid]
    downbeat = int(np.argmax([sum(lows[k::per_bar]) for k in range(per_bar)]))
    return Beats(bpm=round(60 * fps / period, 2), beats=beats, downbeat=downbeat,
                 per_bar=per_bar, onsets_per_sec=round(len(pk) / (len(y) / SR), 2))


@dataclass
class Shot:
    """A source and the span of it worth using (end None = to the end)."""
    source: str
    start: float = 0.0
    end: float | None = None

    @classmethod
    def parse(cls, spec: str) -> "Shot":
        """'clip.mp4' or 'clip.mp4@2-30' (seconds; either side may be blank)."""
        path, _, span = spec.rpartition("@") if "@" in spec else (spec, "", "")
        a, _, b = span.partition("-")
        return cls(str(Path(path).expanduser().resolve()),
                   float(a) if a else 0.0, float(b) if b else None)


def montage(shots: list[Shot], music: str | Path, bars: int = 16,
            pattern: list[int] | None = None, start_bar: int = 0,
            width: int = 1920, height: int = 1080, fps: float = 25.0,
            volume: float = 1.0, fade_out: float = 1.5, log=print) -> Timeline:
    """Cut `shots` to the beat of `music`, starting on a downbeat.

    `pattern` is the shot length in beats, cycled (default: 2 beats each).
    Shots are taken round-robin; each source is walked forward so the same
    clip doesn't repeat a moment until it has been used up, then it wraps.
    List a source more than once to show it more often."""
    pattern = pattern or [2]
    music = str(Path(music).expanduser().resolve())
    bt = detect(music)
    first = bt.downbeat + start_bar * bt.per_bar
    want = bars * bt.per_bar
    if first + want >= len(bt.beats):
        raise ValueError(f"the track has {len(bt.bars)} bars at {bt.bpm} BPM; "
                         f"asked for {bars} from bar {start_bar}")
    log(f"music: {bt.bpm} BPM, {len(bt.bars)} bars, first downbeat at "
        f"{bt.beats[bt.downbeat]:.2f}s")

    # Timeline 0 = the chosen downbeat, on the frame grid.
    src_in = math.floor(bt.beats[first] * fps) / fps
    cut_frames, k, beat = [0], 0, first
    while beat < first + want:
        beat = min(first + want, beat + pattern[k % len(pattern)])
        cut_frames.append(round((bt.beats[beat] - src_in) * fps))
        k += 1

    infos = {s.source: media.probe(s.source) for s in shots}
    cursor = {s.source: s.start for s in shots}     # per source, shared by repeats
    clips = []
    for n, (a, b) in enumerate(zip(cut_frames, cut_frames[1:])):
        s = shots[n % len(shots)]
        length = (b - a) / fps
        if infos[s.source].is_image:                 # a still lasts as long as it's needed
            clips.append(Clip(source=s.source, start=0.0, end=length,
                              note=f"{Path(s.source).stem[:24]} beat {a / fps:.2f}s"))
            continue
        end_limit = min(s.end or infos[s.source].duration, infos[s.source].duration)
        if end_limit - s.start < length:
            raise ValueError(f"{Path(s.source).name}: usable span shorter than one shot")
        if not s.start <= cursor[s.source] <= end_limit - length:
            cursor[s.source] = s.start
        start = math.ceil(cursor[s.source] * fps - 1e-6) / fps   # in-points on the grid
        clips.append(Clip(source=s.source, start=start, end=start + length,
                          note=f"{Path(s.source).stem[:24]} beat {a / fps:.2f}s"))
        cursor[s.source] = start + length

    tl = Timeline(clips=clips, width=width, height=height, fps=fps)
    tl.audio.append(AudioTrack(source=music, src_in=src_in, volume=volume,
                               fade_out=fade_out, duration=cut_frames[-1] / fps))
    log(f"montage: {len(clips)} shots over {bars} bars, {tl.duration:.2f}s")
    return tl
