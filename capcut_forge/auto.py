"""Point capcut-forge at a CapCut project (or a folder of footage) and get an edit.

`gather` finds the footage: what was imported into the draft's Media panel,
what is in a folder, or both. `decide` looks at it — speech or not, music or
not, portrait or landscape — and `build_plan` turns it into a Timeline:

- talking footage (Whisper hears steady speech): pauses, fillers and retakes
  cut, word-timed captions, punch-ins on alternate cuts, b-roll cutaways from
  the other clips, any music ducked underneath;
- b-roll / stills with music: a montage cut on the beat, with camera moves,
  one grade and per-shot matching;
- b-roll / stills without music: the same montage on a steady pace over an
  original ambient bed.

`auto_edit` then writes it straight into the draft (backed up first), or
into a new draft when the name is new.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ambient, beats, captions, grade, match, media, motion, transcribe
from .capcut import build as cc
from .capcut.store import DraftError, DraftStore, forge_home
from .plan import PlanOptions, plan
from .timeline import AudioTrack, Caption, Clip, Overlay, Timeline

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080),
           "4:5": (1080, 1350), "3:4": (1080, 1440), "4:3": (1440, 1080)}
STYLES = ("auto", "talking", "montage")
MOVES = ["push-in", "drift", "pan-right", "pull-out", "pan-left", "orbit"]
# our own outputs (ambient beds, bars, renders), never footage
SKIP = re.compile(r"(^|\.)ambient-(warm|dark|airy|dreamy)\b|\.capcut-forge|^letterbox-|-warm-\d+s\.wav$")


@dataclass
class Footage:
    videos: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    music: list[str] = field(default_factory=list)

    def add(self, path: str | Path) -> None:
        p = str(Path(path).expanduser().resolve())
        bucket = {"video": self.videos, "image": self.images,
                  "audio": self.music}.get(media.kind(p) or "")
        if bucket is not None and p not in bucket and not SKIP.search(Path(p).name):
            bucket.append(p)

    def __bool__(self) -> bool:
        return bool(self.videos or self.images)


@dataclass
class Source:
    name: str                 # draft to write (existing or new)
    exists: bool              # True = write into that draft
    footage: Footage
    notes: list[str] = field(default_factory=list)


def scan(folder: Path, depth: int = 2) -> list[Path]:
    """Media files in a folder (and a couple of levels of subfolders), by name."""
    out = []
    for p in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if p.name.startswith("."):
            continue
        if p.is_dir() and depth > 0 and not DraftStore._is_draft(p):
            out += scan(p, depth - 1)
        elif p.is_file() and media.kind(p):
            out.append(p)
    return out


def _timeline_media(store: DraftStore, name: str) -> list[str]:
    """Media already used on a draft's timeline (fallback when the Media panel
    lists nothing, e.g. drafts capcut-forge itself built)."""
    d = store.load(name)
    rows = d["materials"].get("videos", []) + d["materials"].get("audios", [])
    paths = [store.expand(name, m["path"]) for m in rows if m.get("path")]
    return [p for p in paths if Path(p).exists()]


def gather(ref: str | None, store: DraftStore, folder: str | None = None,
           name: str | None = None) -> Source:
    """Work out which draft to write and which footage to use.

    `ref` is a CapCut draft name (loose: "testy" finds "TestY"), a draft
    folder path, or a folder of footage. `folder` adds footage from a folder
    to a named draft. `name` picks the draft to write when `ref` is a folder
    (default: the folder's name)."""
    notes: list[str] = []
    fp = Footage()
    target, exists = None, False
    if ref and Path(ref).expanduser().is_dir() and not DraftStore._is_draft(Path(ref).expanduser()):
        folder, ref = str(Path(ref).expanduser()), None
    if ref:
        target, exists = store.resolve(ref), True
    elif name:
        try:
            target, exists = store.resolve(name), True
        except DraftError:
            target = name
    elif folder:
        target = Path(folder).expanduser().resolve().name
        exists = store.exists(target)
    else:
        raise DraftError("tell me a CapCut project name or a folder of footage")

    if exists:
        bin_items = store.bin_media(target)
        missing = [m["path"] for m in bin_items if not m["exists"]]
        for m in bin_items:
            if m["exists"]:
                fp.add(m["path"])
        if bin_items:
            notes.append(f"{target}: {len(bin_items) - len(missing)} file(s) from the "
                         "Media panel")
        if missing:
            notes.append(f"skipped {len(missing)} Media-panel file(s) that no longer exist: "
                         + ", ".join(Path(m).name for m in missing[:5]))
    if folder:
        f = Path(folder).expanduser().resolve()
        if not f.is_dir():
            raise DraftError(f"no such folder: {f}")
        try:
            found = scan(f)
        except PermissionError:
            raise media.AccessError(media.access_hint(f)) from None
        for p in found:
            fp.add(p)
        notes.append(f"{f}: {len(found)} media file(s)")
    if exists and not fp:
        for p in _timeline_media(store, target):
            fp.add(p)
        if fp:
            notes.append(f"{target}: using the media already on its timeline")
    media.check_readable(fp.videos + fp.images + fp.music)
    if not fp:
        where = f"the {target!r} project" if exists else "that folder"
        raise DraftError(f"found no video or photos in {where}. Import footage into the "
                         "project's Media panel in CapCut, or give me the folder it's in.")
    return Source(target, exists, fp, notes)


# ------------------------------------------------------------------ analysis


def _speech(path: str, info: media.MediaInfo, model: str, log) -> list[dict] | None:
    """Word list if the clip is someone talking, else None."""
    if not info.has_audio or info.duration < 4 or not transcribe.available():
        return None
    silent = sum(e - s for s, e in media.detect_silences(path, -38.0, 0.6))
    if silent > 0.85 * info.duration:                # essentially no sound
        return None
    words = transcribe.cached(path, model, log=log)["words"]
    rate = len(words) / max(info.duration - silent, 1.0)
    return words if len(words) >= 6 and rate >= 0.8 else None


def _orientation(infos: list[media.MediaInfo]) -> str:
    """'9:16' or '16:9' by the footage's majority (screen time for video)."""
    weight = {"9:16": 0.0, "16:9": 0.0}
    for i in infos:
        if i.width and i.height:
            weight["9:16" if i.height > i.width else "16:9"] += max(i.duration, 3.0)
    return max(weight, key=weight.get) if any(weight.values()) else "9:16"


def _fps(infos: list[media.MediaInfo]) -> float:
    """Timeline rate: the footage's most common rate, on a standard grid."""
    rates = [min((24.0, 25.0, 30.0), key=lambda r: abs(r - (i.fps if i.fps < 48 else i.fps / 2)))
             for i in infos if i.fps]
    return max(set(rates), key=rates.count) if rates else 30.0


@dataclass
class Decision:
    style: str
    width: int
    height: int
    fps: float
    talking: dict[str, list[dict]]      # path -> words
    broll: list[str]                    # videos without speech + stills
    music: str | None
    notes: list[str]


def decide(fp: Footage, style: str = "auto", aspect: str = "auto",
           music: str | None = None, model: str = "small", log=print) -> Decision:
    if style not in STYLES:
        raise ValueError(f"style must be one of {', '.join(STYLES)}")
    notes = []
    infos = {p: media.probe(p) for p in fp.videos + fp.images}
    talking: dict[str, list[dict]] = {}
    if style != "montage":
        for p in fp.videos:
            words = _speech(p, infos[p], model, log)
            if words:
                talking[p] = words
        if style == "talking" and not talking:
            if not transcribe.available():
                raise DraftError("talking-head edits need transcription: "
                                 "uv sync --extra transcribe")
            raise DraftError("didn't hear anyone talking in these videos; "
                             "try style='montage'")
    style = "talking" if talking else "montage"
    broll = [p for p in fp.videos if p not in talking] + fp.images

    if music:
        track = str(Path(music).expanduser().resolve())
    elif fp.music:
        track = max(fp.music, key=lambda p: media.probe(p).duration)
        if len(fp.music) > 1:
            notes.append(f"music: picked the longest track, {Path(track).name}")
    else:
        track = None

    if aspect == "auto":
        lead = [infos[p] for p in talking] or [infos[p] for p in fp.videos] or list(infos.values())
        aspect = _orientation(lead)
        notes.append(f"canvas {aspect} (follows the "
                     f"{'speaker' if talking else 'footage'})")
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be auto or one of {', '.join(ASPECTS)}")
    w, h = ASPECTS[aspect]
    fps = _fps([infos[p] for p in (list(talking) or fp.videos)])
    notes.append(f"style {style}: {len(talking)} talking clip(s), {len(broll)} b-roll/still(s)"
                 + (f", music {Path(track).name}" if track else ""))
    return Decision(style, w, h, fps, talking, broll, track, notes)


# ------------------------------------------------------------------ plans


def _moves(tl: Timeline, intensity: float = 0.7) -> None:
    """Eased camera moves on every clip (stronger on stills, which need them),
    a fade in at the top and a fade out at the end."""
    last = len(tl.clips) - 1
    for i, c in enumerate(tl.clips):
        still = media.kind(c.source) == "image"
        keys = motion.preset(MOVES[i % len(MOVES)], c.duration, tl.fps,
                             1.0 if still else intensity)
        if i in (0, last):
            keys = motion.fade(keys, c.duration, tl.fps,
                               min(0.6, c.duration / 3) if i == 0 else 0,
                               min(1.2, c.duration / 2) if i == last else 0)
        c.keyframes = keys


def _shots(paths: list[str]) -> list[beats.Shot]:
    out = []
    for p in paths:
        info = media.probe(p)
        trim = 0.0 if info.is_image else min(0.5, info.duration * 0.05)   # skip fade-ins
        out.append(beats.Shot(p, trim, None if info.is_image else info.duration - trim))
    return out


def _beat_montage(d: Decision, max_seconds: float, log) -> Timeline:
    bt = beats.detect(d.music)
    beat = 60 / bt.bpm
    n = min((1, 2, 4, 8), key=lambda k: abs(k * beat - 2.2))      # ~2s a shot
    h = max(1, n // 2)
    # vary shot length, with a cycle that doesn't line up with the clip
    # rotation (else every clip would always get the same length)
    pattern = ([n, n, h, h] if len(d.broll) % 4 else [n, h, n]) if n > 1 else [1]
    usable = sum(media.probe(p).duration for p in d.broll if media.kind(p) == "video")
    usable += 3.0 * sum(media.kind(p) == "image" for p in d.broll)
    room = len(bt.beats) - bt.downbeat - 1
    bars = max(1, min(math.ceil(min(max_seconds, max(usable, 8.0)) / (beat * bt.per_bar)),
                      room // bt.per_bar))
    log(f"montage: {bt.bpm} BPM, {n} beat(s) a shot, {bars} bars")
    tl = beats.montage(_shots(d.broll), d.music, bars, pattern, 0, d.width, d.height,
                       d.fps, volume=1.0, fade_out=min(2.0, bars * beat), log=log)
    return tl


def _paced_montage(d: Decision, max_seconds: float, shot: float, log) -> Timeline:
    """Montage without music: even shots, walking forward through each clip."""
    shots = _shots(d.broll)
    usable = sum((s.end or 3.0) - s.start for s in shots)
    total = min(max_seconds, max(usable * 0.8, shot * min(len(shots), 4)))
    count = max(1, round(total / shot))
    frames = max(1, round(shot * d.fps))
    clips, cursor = [], {s.source: s.start for s in shots}
    for i in range(count):
        s = shots[i % len(shots)]
        length = frames / d.fps
        if s.end is None:
            clips.append(Clip(source=s.source, start=0.0, end=length))
            continue
        length = min(length, s.end - s.start)
        if cursor[s.source] + length > s.end:
            cursor[s.source] = s.start
        start = math.ceil(cursor[s.source] * d.fps - 1e-6) / d.fps
        clips.append(Clip(source=s.source, start=start, end=start + length))
        cursor[s.source] = start + length
    return Timeline(clips=clips, width=d.width, height=d.height, fps=d.fps)


def _talking(d: Decision, max_seconds: float | None, hook: str | None,
             caption_style: str | None, log) -> Timeline:
    tl = Timeline(width=d.width, height=d.height, fps=d.fps)
    for path, words in d.talking.items():
        opts = PlanOptions(cut="words", drop_retakes=True, punch_in=1.1,
                           caption_style=caption_style, width=d.width, height=d.height,
                           fps=d.fps, max_gap=0.45)
        part, _ = plan(path, opts, {"words": words}, log=log)
        t0 = tl.duration
        tl.clips += part.clips
        tl.captions += [Caption(c.text, round(c.start + t0, 3), round(c.end + t0, 3),
                                c.style, c.role) for c in part.captions]
    if max_seconds and tl.duration > max_seconds:          # trim whole clips off the end
        while len(tl.clips) > 1 and tl.duration > max_seconds:
            tl.clips.pop()
        tl.captions = [c for c in tl.captions if c.start < tl.duration]
        for c in tl.captions:
            c.end = min(c.end, tl.duration)
    if hook:
        words = [{"word": c.text, "start": c.start, "end": c.end} for c in tl.captions]
        tl.captions.insert(0, captions.hook(hook, words))
    return tl


def _cutaways(tl: Timeline, broll: list[str], every: float = 7.0, length: float = 2.4,
              first: float = 3.5) -> None:
    """B-roll laid over the speaker every few seconds, full-frame and muted,
    keeping clear of the opening hook and the last couple of seconds."""
    if not broll:
        return
    spots = (0.5, 0.2, 0.8, 0.35, 0.65)          # where in a clip each reuse starts
    t, k, uses = first, 0, {}
    while t + length < tl.duration - 2.0:
        src = broll[k % len(broll)]
        info = media.probe(src)
        cover = cc.fill_scale(info, tl.width, tl.height)
        span = 0.0 if info.is_image else max(0.0, info.duration - length)
        src_in = round(span * spots[uses.get(src, 0) % len(spots)], 3)
        uses[src] = uses.get(src, 0) + 1
        tl.overlays.append(Overlay(source=src, at=round(t, 3),
                                   duration=min(length, info.duration or length),
                                   src_in=src_in, scale=cover))
        t += every
        k += 1


def build_plan(src: Source, style: str = "auto", aspect: str = "auto",
               max_seconds: float | None = None, music: str | None = None,
               look: str | None = "auto", caption_style: str | None = "tiktok-raw",
               hook: str | None = None, title: str | None = None, broll: bool = True,
               match_shots: bool = True, model: str = "small",
               log=print) -> tuple[Timeline, Decision]:
    d = decide(src.footage, style, aspect, music, model, log)
    for n in d.notes:
        log(n)
    if d.style == "talking":
        tl = _talking(d, max_seconds, hook, caption_style, log)
        if broll:
            _cutaways(tl, d.broll)
        if d.music:
            tl.audio.append(AudioTrack(source=d.music, volume=0.12, fade_in=1.5,
                                       fade_out=1.5, loop=True))
        look = "clean-bright" if look == "auto" else look
    else:
        if d.music and beats.detect(d.music).rhythmic:
            tl = _beat_montage(d, max_seconds or 60.0, log)
        elif d.music:
            tl = _paced_montage(d, max_seconds or 60.0, 3.0, log)
            tl.audio.append(AudioTrack(source=d.music, volume=1.0, fade_in=1.0,
                                       fade_out=min(2.0, tl.duration / 4), loop=True))
            log("music has no steady beat: paced the cuts evenly instead")
        else:
            tl = _paced_montage(d, max_seconds or 60.0, 2.4, log)
            bed = forge_home() / "ambient" / f"{src.name}-warm-{tl.duration:.0f}s.wav"
            bed.parent.mkdir(parents=True, exist_ok=True)
            ambient.generate(bed, tl.duration, "warm")
            tl.audio.append(AudioTrack(source=str(bed), volume=0.5, duration=tl.duration))
            log("no music found: added an original ambient bed")
        for c in tl.clips:
            c.volume = 0.0                      # the music carries a montage
        _moves(tl)
        look = "soft-cinematic" if look == "auto" else look
        if match_shots and len({c.source for c in tl.clips}) > 1:
            match.match(tl, 0.6, log=lambda *_: None)
            log("matched exposure and colour across shots")
        if hook:
            tl.captions.append(captions.hook(hook, [], min(3.0, tl.duration)))
    if title:
        tl.captions.append(Caption(title, 0.0, min(3.0, tl.duration), "hook", "hook"))
    tl.grade = grade.resolve(look) if look else {}
    if look:
        log(f"grade: {look}")
    return tl, d


def auto_edit(ref: str | None = None, folder: str | None = None, name: str | None = None,
              style: str = "auto", aspect: str = "auto", max_seconds: float | None = None,
              music: str | None = None, look: str | None = "auto",
              caption_style: str | None = "tiktok-raw", hook: str | None = None,
              title: str | None = None, broll: bool = True, match_shots: bool = True,
              replace: bool = False, allow_quit: bool = False, dry_run: bool = False,
              relaunch: bool = True, render: str | None = None,
              store: DraftStore | None = None, log=print) -> dict:
    """Footage in a CapCut project or folder -> an edit, written into CapCut."""
    store = store or DraftStore()
    src = gather(ref, store, folder, name)
    for n in src.notes:
        log(n)
    tl, d = build_plan(src, style, aspect, max_seconds, music, look, caption_style, hook,
                       title, broll, match_shots, log=log)
    plans = forge_home() / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan_path = tl.save(plans / f"{src.name}.plan.json")
    log(f"plan -> {plan_path}")
    if src.exists:
        out = cc.rebuild_draft(tl, src.name, store, allow_quit=allow_quit, dry_run=dry_run,
                               relaunch=relaunch, replace=replace,
                               reason=f"auto edit ({d.style})")
    else:
        out = cc.build_draft(tl, src.name, store, allow_quit=allow_quit, dry_run=dry_run,
                             relaunch=relaunch)
    if render and not dry_run:
        from .render.ffmpeg import render as do_render
        out["render"] = str(do_render(tl, render, preset="veryfast"))
    return {**out, "draft": src.name, "new_draft": not src.exists, "style": d.style,
            "canvas": f"{tl.width}x{tl.height}", "fps": tl.fps,
            "seconds": round(tl.duration, 2), "plan": str(plan_path),
            "notes": src.notes + d.notes}
