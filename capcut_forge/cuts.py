"""Decide what to keep: silence-based and transcript-based auto-cutting.

Ranges are (start, end) seconds on the source. `cuts.json` keeps the shape
the old bridge kit read, so existing EDLs still load.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .timeline import Clip

Range = tuple[float, float]

FILLERS = {"um", "uh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}


def _merge(ranges: list[Range], join_gap: float = 0.0) -> list[Range]:
    out: list[Range] = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + join_gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _subtract(ranges: list[Range], holes: list[Range]) -> list[Range]:
    out = []
    for s, e in ranges:
        pieces = [(s, e)]
        for hs, he in holes:
            nxt = []
            for ps, pe in pieces:
                if he <= ps or hs >= pe:
                    nxt.append((ps, pe))
                    continue
                if hs > ps:
                    nxt.append((ps, hs))
                if he < pe:
                    nxt.append((he, pe))
            pieces = nxt
        out.extend(pieces)
    return out


def _tidy(ranges: list[Range], duration: float, pad: float, min_keep: float,
          join_gap: float) -> list[Range]:
    padded = [(max(0.0, s - pad), min(duration, e + pad)) for s, e in ranges]
    return [(round(s, 3), round(e, 3)) for s, e in _merge(padded, join_gap)
            if e - s >= min_keep]


def from_silences(duration: float, silences: list[Range], pad: float = 0.08,
                  min_keep: float = 0.25, join_gap: float = 0.15) -> list[Range]:
    """Keep everything that isn't silence."""
    return _tidy(_subtract([(0.0, duration)], silences), duration, pad, min_keep, join_gap)


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def from_words(words: list[dict], duration: float, max_gap: float = 0.5,
               pad: float = 0.08, min_keep: float = 0.25, drop_fillers: bool = True,
               extra_fillers: set[str] | None = None,
               drop_retakes: bool = False) -> list[Range]:
    """Keep spoken words, cutting pauses longer than `max_gap`, filler words,
    and (optionally) repeated restarts like "so I — so I went"."""
    fillers = FILLERS | (extra_fillers or set())
    kept = [w for w in words if not (drop_fillers and _norm(w["word"]) in fillers)]
    if drop_retakes:
        kept = _drop_retakes(kept)
    spans = [(w["start"], w["end"]) for w in kept if w["end"] > w["start"]]
    # Joining short pauses would glue a dropped word back in, so cut the
    # dropped words out again after merging.
    keep_ids = {id(w) for w in kept}
    holes = [(w["start"], w["end"]) for w in words if id(w) not in keep_ids]
    ranges = _subtract(_tidy(spans, duration, pad, 0.0, max_gap), holes)
    return [(round(s, 3), round(e, 3)) for s, e in ranges if e - s >= min_keep]


def _drop_retakes(words: list[dict], window: int = 8) -> list[dict]:
    """Remove a short phrase that is immediately repeated (a false start).
    Keeps the LAST take, which is usually the clean one."""
    toks = [_norm(w["word"]) for w in words]
    drop = set()
    i = 0
    while i < len(words):
        for n in range(window, 0, -1):
            a, b = toks[i:i + n], toks[i + n:i + 2 * n]
            if len(a) == n and a == b and all(a):
                drop.update(range(i, i + n))
                i += n - 1
                break
        i += 1
    return [w for k, w in enumerate(words) if k not in drop]


def to_clips(ranges: list[Range], source: str) -> list[Clip]:
    return [Clip(source=source, start=s, end=e) for s, e in ranges]


def save(ranges: list[Range], source: str | Path, path: str | Path,
         fps: float = 30.0, words: list[dict] | None = None) -> Path:
    """Write cuts.json (compatible with the old kit's EDL format)."""
    name = Path(source).name
    segs = []
    for s, e in ranges:
        note = ""
        if words:
            note = " ".join(w["word"].strip() for w in words
                            if w["start"] >= s and w["end"] <= e)[:120]
        segs.append({"clip": name, "start": s, "end": e, "note": note})
    path = Path(path)
    path.write_text(json.dumps({"clip": name, "source": str(Path(source).resolve()),
                                "fps": fps, "segments": segs}, indent=2), encoding="utf-8")
    return path


def load(path: str | Path) -> list[Clip]:
    """Read cuts.json. Clip files resolve against `source`, then the JSON's
    folder, then a sibling raw/ folder (the old kit's job layout)."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    clips = []
    for s in data["segments"]:
        name = s.get("clip") or data.get("clip")
        candidates = [data.get("source") if Path(data.get("source") or "").name == name else None,
                      path.parent / name, path.parent.parent / "raw" / name, name]
        src = next((str(Path(c)) for c in candidates if c and Path(c).exists()), name)
        clips.append(Clip(source=src, start=float(s["start"]), end=float(s["end"]),
                          zoom=float(s.get("zoom", 1.0)), note=s.get("note", "")))
    return clips
