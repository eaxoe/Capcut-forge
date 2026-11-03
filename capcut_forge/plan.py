"""One call from raw footage to a full edit plan."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import ambient, captions, cuts, grade, media, transcribe
from .timeline import AudioTrack, Clip, Overlay, Timeline


@dataclass
class PlanOptions:
    # cutting
    cut: str = "auto"                 # auto | silence | words | none
    noise_db: float = -35.0
    min_silence: float = 0.4
    max_gap: float = 0.5
    pad: float = 0.08
    drop_fillers: bool = True
    drop_retakes: bool = False
    # look
    width: int = 1080
    height: int = 1920
    fps: float = 30.0
    punch_in: float = 1.0             # >1 alternates clip zoom to hide jump cuts
    # captions
    caption_style: str | None = "tiktok-raw"
    corrections: str | None = None
    hook_text: str | None = None
    hook_end: float | None = None
    # colour
    grade: str | None = None          # preset name from capcut_forge.grade
    letterbox: float | None = None    # cinema bars, e.g. 2.39
    # audio
    ambient: str | None = None        # generate an original ambient bed: warm|dark|airy|dreamy
    ambient_volume: float = 0.35
    ambient_dir: str | None = None    # where to write it (default: next to the video)
    music: str | None = None
    music_volume: float = 0.15
    music_fade: float = 1.5
    sfx_on_cuts: str | None = None    # a whoosh/pop placed on every punch-in
    sfx_volume: float = 0.6
    overlays: list[Overlay] = field(default_factory=list)


def plan(video: str | Path, opts: PlanOptions, transcript: dict | None = None,
         clips: list[Clip] | None = None, log=print) -> tuple[Timeline, dict | None]:
    video = str(Path(video).expanduser().resolve())
    info = media.probe(video)
    words = (transcript or {}).get("words")

    if clips is None:
        mode = opts.cut
        if mode == "auto":
            mode = "words" if words else "silence"
        if mode == "words":
            if not words:
                raise ValueError("--cut words needs a transcript (run `capcut-forge transcribe`)")
            ranges = cuts.from_words(words, info.duration, opts.max_gap, opts.pad,
                                     drop_fillers=opts.drop_fillers,
                                     drop_retakes=opts.drop_retakes)
        elif mode == "silence":
            ranges = cuts.from_silences(info.duration,
                                        media.detect_silences(video, opts.noise_db,
                                                              opts.min_silence), opts.pad)
        else:
            ranges = [(0.0, info.duration)]
        clips = cuts.to_clips(ranges, video)
        kept = sum(e - s for s, e in ranges)
        log(f"cut ({mode}): kept {len(ranges)} span(s), {kept:.1f}s of {info.duration:.1f}s")

    if opts.punch_in > 1.0:
        for i, c in enumerate(clips):
            c.zoom = opts.punch_in if i % 2 else 1.0

    tl = Timeline(clips=clips, width=opts.width, height=opts.height, fps=opts.fps,
                  overlays=list(opts.overlays), grade=grade.resolve(opts.grade),
                  letterbox=opts.letterbox)

    if words and opts.caption_style:
        tl_words = transcribe.remap(words, clips)
        fixes = captions.load_corrections(opts.corrections)
        tl.captions = captions.build(tl_words, opts.caption_style, fixes)
        if opts.hook_text:
            tl.captions.insert(0, captions.hook(opts.hook_text, tl_words, opts.hook_end))
        log(f"captions: {len(tl.captions)} cue(s) in style {opts.caption_style!r}")
    elif opts.hook_text:
        tl.captions.append(captions.hook(opts.hook_text, [], opts.hook_end))

    if opts.music:
        tl.audio.append(AudioTrack(source=str(Path(opts.music).resolve()), volume=opts.music_volume,
                                   fade_in=opts.music_fade, fade_out=opts.music_fade,
                                   loop=True))
    if opts.ambient:
        folder = Path(opts.ambient_dir or Path(video).parent)
        bed = ambient.generate(folder / f"{Path(video).stem}.ambient-{opts.ambient}.wav",
                               tl.duration, opts.ambient)
        tl.audio.append(AudioTrack(source=str(bed), volume=opts.ambient_volume,
                                   duration=tl.duration))
        log(f"ambient: {opts.ambient} bed, {tl.duration:.1f}s -> {bed}")
    if opts.sfx_on_cuts:
        sfx = str(Path(opts.sfx_on_cuts).resolve())
        for start, c in zip(tl.clip_starts(), tl.clips):
            if c.zoom > 1.0:
                tl.audio.append(AudioTrack(source=sfx, at=max(0.0, start - 0.05),
                                           volume=opts.sfx_volume))
    return tl, transcript
