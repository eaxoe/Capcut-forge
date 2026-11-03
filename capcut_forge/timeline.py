"""The edit plan: one backend-neutral description of a video.

Everything upstream (auto-cut, transcription, captions) produces a Timeline;
everything downstream (CapCut draft builder, ffmpeg renderer) consumes one.
Times are seconds. `Clip` times are on the SOURCE file; everything else is on
the output timeline.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class Clip:
    """One kept span of a source file, laid end to end on the main track."""
    source: str
    start: float          # seconds into the source
    end: float
    zoom: float = 1.0     # static punch-in (1.0 = none)
    x: float = 0.0        # reframe, CapCut-normalised like Overlay: -1..1, +y is up
    y: float = 0.0        # (e.g. y -0.4 shows more of the top of a vertical clip)
    volume: float = 1.0   # the clip's own sound (0 = muted, e.g. under montage music)
    note: str = ""
    # Per-shot grade, ADDED to the timeline's grade (shot matching, see capcut_forge.match).
    grade: dict[str, float] = field(default_factory=dict)
    # Animation: property -> [[seconds from clip start, value], ...]. Properties:
    # scale (multiplies zoom), x, y (canvas-normalised), rotation (deg), opacity.
    keyframes: dict[str, list[list[float]]] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Overlay:
    """A video/image placed above the main track (e.g. an alpha .mov graphic)."""
    source: str
    at: float
    duration: float | None = None   # None = the media's own length
    src_in: float = 0.0
    layer: int = 1
    x: float = 0.0                  # CapCut-normalised: -1..1, +y is up
    y: float = 0.0
    scale: float = 1.0
    mute: bool = True


@dataclass
class Caption:
    text: str
    start: float
    end: float
    style: str = "tiktok-raw"
    role: str = "caption"           # "caption" or "hook"


@dataclass
class AudioTrack:
    """Music bed or sound effect."""
    source: str
    at: float = 0.0
    duration: float | None = None   # None = until the video ends (music) / own length
    src_in: float = 0.0
    volume: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    loop: bool = False


@dataclass
class Timeline:
    clips: list[Clip] = field(default_factory=list)
    overlays: list[Overlay] = field(default_factory=list)
    captions: list[Caption] = field(default_factory=list)
    audio: list[AudioTrack] = field(default_factory=list)
    grade: dict[str, float] = field(default_factory=dict)   # see capcut_forge.grade
    letterbox: float | None = None    # e.g. 2.39 for cinema bars over the picture
    width: int = 1080
    height: int = 1920
    fps: float = 30.0

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def clip_grade(self, clip: "Clip") -> dict[str, float]:
        """The timeline look plus this shot's matching offsets, clamped."""
        out = dict(self.grade)
        for k, v in clip.grade.items():
            out[k] = out.get(k, 0.0) + v
        return {k: round(max(-1.0, min(1.0, v)), 4) for k, v in out.items() if abs(v) > 1e-4}

    def clip_starts(self) -> list[float]:
        """Timeline start of each clip."""
        out, t = [], 0.0
        for c in self.clips:
            out.append(t)
            t += c.duration
        return out

    # ------------------------------------------------------------ persistence

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        def build(kind, rows):
            known = {f.name for f in fields(kind)}
            return [kind(**{k: v for k, v in r.items() if k in known}) for r in rows]

        return cls(
            clips=build(Clip, d.get("clips", [])),
            overlays=build(Overlay, d.get("overlays", [])),
            captions=build(Caption, d.get("captions", [])),
            audio=build(AudioTrack, d.get("audio", [])),
            width=d.get("width", 1080),
            height=d.get("height", 1920),
            fps=d.get("fps", 30.0),
            grade=dict(d.get("grade") or {}),
            letterbox=d.get("letterbox"),
        )

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Timeline":
        path = Path(path)
        tl = cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        tl.resolve_paths(path.parent)
        return tl

    def resolve_paths(self, base: Path) -> None:
        """Make relative media paths absolute against the plan file's folder."""
        for item in [*self.clips, *self.overlays, *self.audio]:
            p = Path(item.source).expanduser()
            item.source = str(p if p.is_absolute() else (base / p).resolve())

    def validate(self) -> list[str]:
        """Human-readable problems; empty list means the plan is buildable."""
        problems = []
        if not self.clips:
            problems.append("no clips on the main track")
        for i, c in enumerate(self.clips):
            if c.end <= c.start:
                problems.append(f"clip {i}: end {c.end} <= start {c.start}")
            if c.zoom < 1.0:
                problems.append(f"clip {i}: zoom {c.zoom} < 1.0")
        for kind, items in (("clip", self.clips), ("overlay", self.overlays),
                            ("audio", self.audio)):
            for i, item in enumerate(items):
                if not Path(item.source).exists():
                    problems.append(f"{kind} {i}: missing file {item.source}")
        for i, c in enumerate(self.captions):
            if c.end <= c.start:
                problems.append(f"caption {i}: end <= start ({c.text!r})")
        return problems
