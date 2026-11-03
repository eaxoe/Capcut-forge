import subprocess

import numpy as np
import pytest

from capcut_forge import beats
from capcut_forge.timeline import Timeline

SR = 44100


def groove(path, bpm=114.0, bars=24, lead=0.3):
    """A syncopated funk-style loop: kick on 1 and the 'and' of 2, snare on
    2 and 4, 16th hats. Kicks off the beat are what fool plain autocorrelation."""
    beat = 60 / bpm
    n = int(SR * (lead + bars * 4 * beat + 1))
    y = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(0)

    def hit(t, kind):
        i = int((lead + t) * SR)
        k = np.arange(int(0.12 * SR)) / SR
        if kind == "kick":
            s = np.sin(2 * np.pi * (55 + 60 * np.exp(-k * 40)) * k) * np.exp(-k * 25)
        elif kind == "snare":
            s = rng.normal(0, 0.5, len(k)) * np.exp(-k * 30)
        else:
            s = rng.normal(0, 0.15, len(k)) * np.exp(-k * 120)
        y[i:i + len(s)] += s[: n - i]

    for bar in range(bars):
        t0 = bar * 4 * beat
        hit(t0, "kick"); hit(t0 + 1.5 * beat, "kick"); hit(t0 + 2.75 * beat, "kick")
        hit(t0 + beat, "snare"); hit(t0 + 3 * beat, "snare")
        for s16 in range(16):
            hit(t0 + s16 * beat / 4, "hat")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                    "-i", "-", str(path)], input=(y * 0.5).tobytes(), check=True)
    return lead, beat


@pytest.fixture(scope="module")
def funk(tmp_path_factory):
    path = tmp_path_factory.mktemp("beats") / "funk.wav"
    lead, beat = groove(path)
    return path, lead, beat


def test_tempo_is_the_groove_not_its_syncopation(funk):
    path, lead, beat = funk
    b = beats.detect(path)
    assert b.bpm == pytest.approx(114, abs=0.3)


def test_beats_and_bars_land_on_the_grid(funk):
    path, lead, beat = funk
    b = beats.detect(path)
    for t in b.beats[4:-4]:
        k = round((t - lead) / beat)
        assert abs(t - (lead + k * beat)) < 0.025
    assert abs(b.bars[0] - lead) % (4 * beat) < 0.025      # bar 1 is the first kick


def test_montage_cuts_on_beats_and_frames(funk, media_dir):
    path, lead, beat = funk
    clip = str(media_dir / "talk.mp4")
    tl = beats.montage([beats.Shot.parse(clip)], path, bars=2, pattern=[2, 1, 1],
                       width=640, height=360, fps=25.0, log=lambda *_: None)
    assert isinstance(tl, Timeline) and len(tl.clips) == 6
    src_in = tl.audio[0].src_in
    t = 0.0
    for c in tl.clips:
        assert (c.end - c.start) * 25 == pytest.approx(round((c.end - c.start) * 25), abs=1e-6)
        song = src_in + t
        k = round((song - lead) / beat)
        assert abs(song - (lead + k * beat)) <= 0.5 / 25 + 0.025   # half a frame + detect
        t += c.duration
    assert tl.duration == pytest.approx(8 * beat, abs=1 / 25)
    # the one source is walked forward, never reusing a moment until it wraps
    assert tl.clips[1].start >= tl.clips[0].end - 1e-6


def test_shot_spec_parses_spans(tmp_path):
    s = beats.Shot.parse(str(tmp_path / "a.mp4") + "@2-30.5")
    assert (s.start, s.end) == (2.0, 30.5)
    assert beats.Shot.parse(str(tmp_path / "a.mp4")).end is None
