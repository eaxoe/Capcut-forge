"""capcut-forge command line."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import ambient, auto, beats, captions, cuts, grade, match, media, motion, styles, transcribe
from .capcut import app, build as cc
from .capcut.store import DraftError, DraftStore
from .plan import PlanOptions, plan
from .timeline import AudioTrack, Caption, Overlay, Timeline


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False) if not isinstance(obj, str) else obj)


def _size(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def _overlay(spec: str) -> Overlay:
    """FILE@SECONDS[:LAYER]"""
    path, _, rest = spec.rpartition("@")
    at, _, layer = rest.partition(":")
    return Overlay(source=str(Path(path).resolve()), at=float(at), layer=int(layer or 1))


# ------------------------------------------------------------------ pipeline


def add_plan_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("cutting")
    g.add_argument("--transcript", help="transcript.json from `capcut-forge transcribe`")
    g.add_argument("--cuts", help="use an existing cuts.json instead of auto-cutting")
    g.add_argument("--cut", choices=["auto", "silence", "words", "none"], default="auto",
                   help="auto = words if a transcript is given, else silence")
    g.add_argument("--noise-db", type=float, default=-35.0)
    g.add_argument("--min-silence", type=float, default=0.4)
    g.add_argument("--max-gap", type=float, default=0.5,
                   help="pauses longer than this are cut (words mode)")
    g.add_argument("--pad", type=float, default=0.08)
    g.add_argument("--keep-fillers", action="store_true")
    g.add_argument("--drop-retakes", action="store_true",
                   help="remove immediately repeated false starts")
    g = p.add_argument_group("look")
    g.add_argument("--size", type=_size, default=(1080, 1920), help="WxH, default 1080x1920")
    g.add_argument("--fps", type=float, default=30.0)
    g.add_argument("--punch-in", type=float, default=1.0,
                   help="alternate zoom on every other cut, e.g. 1.12")
    g.add_argument("--style", default="tiktok-raw",
                   help=f"caption style ({', '.join(styles.PRESETS)}) or 'none'")
    g.add_argument("--corrections", help="JSON word-fix map for captions")
    g.add_argument("--hook", help="hook card text over the opening line")
    g.add_argument("--hook-end", type=float)
    g.add_argument("--overlay", action="append", default=[], metavar="FILE@SEC[:LAYER]")
    g = p.add_argument_group("audio")
    g.add_argument("--music")
    g.add_argument("--music-volume", type=float, default=0.15)
    g.add_argument("--sfx", help="sound effect placed on every punch-in")
    g.add_argument("--ambient", choices=list(ambient.MOODS),
                   help="generate an original ambient bed under the video")
    g.add_argument("--ambient-volume", type=float, default=0.35)
    g.add_argument("--ambient-dir", help="where to save the ambient bed")
    g = p.add_argument_group("colour")
    g.add_argument("--grade", choices=list(grade.PRESETS),
                   help="colour grade (CapCut Adjust sliders / ffmpeg in renders)")
    g.add_argument("--letterbox", type=float, metavar="ASPECT",
                   help="cinema bars, e.g. 2.39")


def options(a) -> PlanOptions:
    return PlanOptions(
        cut=a.cut, noise_db=a.noise_db, min_silence=a.min_silence, max_gap=a.max_gap,
        pad=a.pad, drop_fillers=not a.keep_fillers, drop_retakes=a.drop_retakes,
        width=a.size[0], height=a.size[1], fps=a.fps, punch_in=a.punch_in,
        caption_style=None if a.style == "none" else a.style, corrections=a.corrections,
        hook_text=a.hook, hook_end=a.hook_end, music=a.music, music_volume=a.music_volume,
        sfx_on_cuts=a.sfx, overlays=[_overlay(s) for s in a.overlay], grade=a.grade,
        ambient=a.ambient, ambient_volume=a.ambient_volume, ambient_dir=a.ambient_dir,
        letterbox=a.letterbox)


def _timeline_from_args(a, log=print) -> Timeline:
    tr = transcribe.load(a.transcript) if a.transcript else None
    clips = cuts.load(a.cuts) if a.cuts else None
    tl, _ = plan(a.video, options(a), tr, clips, log=log)
    return tl


def cmd_transcribe(a):
    out = Path(a.out or Path(a.video).with_suffix(".transcript.json"))
    t = transcribe.transcribe(a.video, a.model, a.language)
    transcribe.save(t, out)
    print(f"{len(t['words'])} words ({t['language']}) -> {out}")


def cmd_autocut(a):
    a.style, a.hook, a.music, a.sfx, a.overlay, a.cuts = "none", None, None, None, [], None
    tl = _timeline_from_args(a)
    tr = transcribe.load(a.transcript) if a.transcript else None
    out = cuts.save([(c.start, c.end) for c in tl.clips], a.video,
                    a.out or Path(a.video).with_suffix(".cuts.json"), a.fps,
                    (tr or {}).get("words"))
    print(f"-> {out}")


def cmd_plan(a):
    tl = _timeline_from_args(a)
    out = tl.save(a.out or Path(a.video).with_suffix(".plan.json"))
    print(f"{len(tl.clips)} clips, {tl.duration:.1f}s -> {out}")


def cmd_render(a):
    from .render.ffmpeg import render
    tl = Timeline.load(a.plan)
    out = render(tl, a.out or Path(a.plan).with_suffix(".mp4"), a.until, a.crf, a.preset)
    print(f"rendered -> {out}")


def cmd_make(a):
    """Raw video -> plan (+ render and/or CapCut draft) in one go."""
    video = Path(a.video)
    if a.transcribe and not a.transcript:
        t = transcribe.transcribe(video, a.model, a.language)
        a.transcript = str(transcribe.save(t, video.with_suffix(".transcript.json")))
        print(f"transcript: {len(t['words'])} words -> {a.transcript}")
    tl = _timeline_from_args(a)
    plan_path = tl.save(a.plan_out or video.with_suffix(".plan.json"))
    print(f"plan -> {plan_path}")
    if a.render:
        from .render.ffmpeg import render
        print(f"rendered -> {render(tl, a.render, a.until)}")
    if a.capcut:
        _print(cc.build_draft(tl, a.capcut, _store(a), allow_quit=a.quit_app))


def cmd_match(a):
    tl = Timeline.load(a.plan)
    if a.look:
        tl.grade = grade.resolve(a.look)
    match.match(tl, a.strength, a.target_luma)
    print(f"-> {tl.save(a.out or a.plan)}")


def cmd_motion(a):
    tl = Timeline.load(a.plan)
    moves = a.moves.split(",")
    for i, c in enumerate(tl.clips):
        keys = motion.preset(moves[i % len(moves)], c.duration, tl.fps, a.intensity)
        first, last = i == 0, i == len(tl.clips) - 1
        if (first and a.fade_in) or (last and a.fade_out):
            keys = motion.fade(keys, c.duration, tl.fps, a.fade_in if first else 0,
                               a.fade_out if last else 0)
        c.keyframes = keys
        print(f"clip {i}: {moves[i % len(moves)]}"
              + (" + fade in" if first and a.fade_in else "")
              + (" + fade out" if last and a.fade_out else ""))
    if a.letterbox is not None:
        tl.letterbox = a.letterbox or None
    print(f"-> {tl.save(a.out or a.plan)}")


def cmd_auto(a):
    """CapCut project name or footage folder -> edit written into CapCut."""
    ref, folder = a.target, a.folder
    _print(auto.auto_edit(ref, folder, a.name, a.style, a.aspect, a.max_seconds, a.music,
                          None if a.look == "none" else a.look,
                          None if a.captions == "none" else a.captions, a.hook, a.title,
                          not a.no_broll, not a.no_match, a.replace, a.quit_app, a.dry_run,
                          not a.no_relaunch, a.render, _store(a)))


def cmd_cc_restyle(a):
    tl = Timeline.load(a.plan)
    _edit(a, lambda d, ctx: cc.restyle(d, ctx, tl), f"restyle from {Path(a.plan).name}")


def cmd_beats(a):
    b = beats.detect(a.music)
    print(f"{b.bpm} BPM, {len(b.beats)} beats, {len(b.bars)} bars, "
          f"first downbeat {b.beats[b.downbeat]:.3f}s")
    if a.out:
        Path(a.out).write_text(json.dumps({"bpm": b.bpm, "beats": b.beats,
                                           "bars": b.bars}, indent=1), encoding="utf-8")
        print(f"-> {a.out}")


def cmd_montage(a):
    tl = beats.montage([beats.Shot.parse(s) for s in a.clips], a.music, a.bars,
                       [int(x) for x in a.pattern.split(",")], a.start_bar,
                       a.width, a.height, a.fps, a.volume, a.fade_out)
    if a.look:
        tl.grade = grade.resolve(a.look)
    if a.match:
        match.match(tl)
    print(f"-> {tl.save(a.out)}")


def cmd_ambient(a):
    print(f"-> {ambient.generate(a.out, a.duration, a.mood)}")


def cmd_srt(a):
    tl = Timeline.load(a.plan)
    print(f"-> {captions.to_srt(tl.captions, a.out or Path(a.plan).with_suffix('.srt'))}")


def cmd_doctor(a):
    store = _store(a)
    checks = {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "CapCut installed": str(app.executable() or "not found"),
        "CapCut running": app.is_running(),
        "draft root": f"{store.root} ({'ok' if store.root.exists() else 'MISSING'})",
        "drafts": len(store.list()) if store.root.exists() else 0,
        "backups dir": str(store.backups),
    }
    try:
        import faster_whisper  # noqa: F401
        checks["transcribe extra"] = "installed"
    except ImportError:
        checks["transcribe extra"] = "not installed (uv sync --extra transcribe)"
    try:
        from .capcut import live
        checks["live extra"] = ("installed" if live.HAVE_AX
                                else "not installed (uv sync --extra live)")
        if live.HAVE_AX:
            checks["accessibility permission"] = bool(live.AXIsProcessTrusted())
    except Exception as e:  # noqa: BLE001
        checks["live extra"] = f"error: {e}"
    for k, v in checks.items():
        print(f"{k:26} {v}")


# ------------------------------------------------------------------ capcut file lane


def _store(a) -> DraftStore:
    store = DraftStore(Path(a.draft_root)) if getattr(a, "draft_root", None) else DraftStore()
    if getattr(a, "draft", None):           # loose names: "testy" finds "TestY"
        a.draft = store.resolve(a.draft)
    return store


def _edit(a, mutate, reason):
    store = _store(a)
    _print(cc.edit(a.draft, mutate, reason, store, allow_quit=a.quit_app,
                   dry_run=a.dry_run, relaunch=not a.no_relaunch))


def cmd_cc_build(a):
    tl = Timeline.load(a.plan)
    store = _store(a)
    if a.into_existing:
        _print(cc.rebuild_draft(tl, store.resolve(a.name), store, allow_quit=a.quit_app,
                                dry_run=a.dry_run, relaunch=not a.no_relaunch,
                                replace=a.replace, reason=f"build from {Path(a.plan).name}"))
        return
    _print(cc.build_draft(tl, a.name, store, allow_quit=a.quit_app, dry_run=a.dry_run,
                          relaunch=not a.no_relaunch))


def cmd_cc_ls(a):
    store = _store(a)
    for n in store.names():
        try:
            secs = store.load(n).get("duration", 0) / 1e6
        except (OSError, ValueError):
            continue
        bin_items = [m for m in store.bin_media(n) if m["exists"]]
        kinds = ", ".join(f"{sum(m['kind'] == k for m in bin_items)} {k}"
                          for k in ("video", "image", "audio")
                          if any(m["kind"] == k for m in bin_items)) or "no media"
        print(f"{n:30} {secs:8.2f}s  {kinds}")


def cmd_cc_inspect(a):
    _print(cc.summary(_store(a).load(a.draft)))


def cmd_cc_overlay(a):
    ov = Overlay(source=str(Path(a.media).resolve()), at=a.at, duration=a.dur, src_in=a.src,
                 layer=a.layer, x=a.x, y=a.y, scale=a.scale, mute=not a.unmute)
    _edit(a, lambda d, ctx: cc.add_overlay(ctx, ov), f"add-overlay {Path(a.media).name}@{a.at}")


def cmd_cc_text(a):
    cap = Caption(text=a.text, start=a.at, end=a.at + a.dur, style=a.style,
                  role="hook" if a.style == "hook" else "caption")
    _edit(a, lambda d, ctx: cc.add_caption(ctx, cap), f"add-text {a.text[:30]!r}@{a.at}")


def cmd_cc_captions(a):
    tl = Timeline.load(a.plan)

    def mutate(d, ctx):
        for c in tl.captions:
            cc.add_caption(ctx, c)
    _edit(a, mutate, f"add-captions from {Path(a.plan).name}")


def cmd_cc_audio(a):
    tr = AudioTrack(source=str(Path(a.media).resolve()), at=a.at, duration=a.dur,
                    volume=a.volume, fade_in=a.fade_in, fade_out=a.fade_out, loop=a.loop)

    def mutate(d, ctx):
        end = max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                   for _, s in cc.segments(d, "main")), default=0)
        cc.add_audio(ctx, tr, end)
    _edit(a, mutate, f"add-audio {Path(a.media).name}@{a.at}")


def cmd_cc_transform(a):
    def mutate(d, ctx):
        cc.transform(cc.pick(d, a.track, a.index)[1], a.scale, a.x, a.y, a.rotate, a.opacity)
    _edit(a, mutate, f"transform {a.track}[{a.index}]")


def cmd_cc_keyframe(a):
    from .capcut import schema
    vals = {k: v for k, v in (("scale", a.scale), ("x", a.x), ("y", a.y),
                              ("rotation", a.rotate), ("opacity", a.opacity),
                              ("volume", a.volume)) if v is not None}
    if not vals:
        sys.exit("nothing to animate: pass --scale/--x/--y/--rotate/--opacity/--volume")

    def mutate(d, ctx):
        seg = cc.pick(d, a.track, a.index)[1]
        for prop, v in vals.items():
            schema.add_keyframe(seg, prop, schema.us(a.at), v, a.curve)
    _edit(a, mutate, f"keyframe {a.track}[{a.index}]@{a.at}")


def cmd_cc_clear_kf(a):
    def mutate(d, ctx):
        cc.pick(d, a.track, a.index)[1]["common_keyframes"] = []
    _edit(a, mutate, f"clear-keyframes {a.track}[{a.index}]")


def cmd_cc_grade(a):
    g = grade.resolve(a.preset, **{s: getattr(a, s) for s in grade.SLIDERS})
    if not g:
        sys.exit("nothing to apply: pass --preset and/or slider values")
    roles = tuple(a.tracks.split(","))
    _edit(a, lambda d, ctx: cc.apply_grade(d, ctx, g, roles),
          f"grade {a.preset or 'custom'} on {a.tracks}")


def cmd_cc_remove(a):
    _edit(a, lambda d, ctx: cc.remove(d, a.track, a.index), f"remove {a.track}[{a.index}]")


def cmd_cc_backups(a):
    for b in _store(a).list_backups(a.draft):
        reason = (b / "reason.txt").read_text(encoding="utf-8") if (b / "reason.txt").exists() else ""
        print(f"{b.name}  {reason}")


def cmd_cc_restore(a):
    store = _store(a)
    was_running = store.is_live and app.ensure_closed(a.quit_app)
    snap = store.restore(a.draft, a.backup)
    print(f"restored {a.draft} from {snap.name}")
    if was_running:
        app.launch()


# ------------------------------------------------------------------ live lane


def cmd_live(a):
    from .capcut import live
    c, r = a.action, a.args
    if c == "launch":
        app.launch()
    elif c == "quit":
        app.quit()
    elif c == "open":
        print(f"opened {r[0]}, duration {live.open_draft(r[0])}")
    elif c == "seek":
        landed = live.seek(float(r[0]), a.fps)
        print(f"playhead {landed:.3f}s (target {float(r[0]):.3f}s)")
    elif c == "play":
        live.click("PlayerPlayBtn" if live.find("PlayerPlayBtn") else "PlayerPauseBtn")
    elif c == "playhead":
        print(" / ".join(live.playhead()))
    elif c == "clips":
        for n, x, y, w, h in live.clips():
            print(f"{n}  x={x:.0f} y={y:.0f} w={w:.0f} h={h:.0f}")
    elif c == "select":
        print("selected", live.select(int(r[0])))
    elif c == "split":
        if r:
            live.seek(float(r[0]), a.fps)
        live.click("cutoff")
    elif c == "delete":
        live.select(int(r[0]))
        live.click("del")
    elif c in ("undo", "redo"):
        live.click(c)
    elif c in ("trim-left", "trim-right"):
        live.click("cutLeft" if c == "trim-left" else "cutRight")
    elif c == "marker":
        live.click("mark")
    elif c == "zoomfit":
        live.click("quicklyAdjustZoomFit")
    elif c == "save":
        live.key("cmd+s")
    elif c == "shot":
        print("wrote", live.shot(r[0] if r else "capcut.png"))
    elif c == "dump":
        for _, name, g in live.elements(r[0] if r else None):
            if name:
                print(name + (f"  [{g[0]:.0f},{g[1]:.0f} {g[2]:.0f}x{g[3]:.0f}]" if g else ""))
    elif c == "click":
        print("clicked", live.click(r[0]))
    elif c == "key":
        live.key(r[0], a.times)
    elif c == "export":
        out = live.export(a.to, a.timeout)
        info = media.probe(out)
        print(f"exported {out}  {out.stat().st_size / 1048576:.1f} MB  {info.duration:.2f}s")
    else:
        sys.exit(f"unknown live action {c}")


LIVE_ACTIONS = ["launch", "quit", "open", "seek", "play", "playhead", "clips", "select",
                "split", "delete", "undo", "redo", "trim-left", "trim-right", "marker",
                "zoomfit", "save", "shot", "dump", "click", "key", "export"]


# ------------------------------------------------------------------ parser


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="capcut-forge", description=__doc__)
    p.add_argument("--draft-root", help="CapCut draft folder (default: CapCut's own)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor", help="check tools, permissions and CapCut")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("transcribe", help="local word-level transcript (Whisper)")
    s.add_argument("video"); s.add_argument("-o", "--out")
    s.add_argument("--model", default="small"); s.add_argument("--language")
    s.set_defaults(fn=cmd_transcribe)

    s = sub.add_parser("autocut", help="write cuts.json (silence / pauses / fillers removed)")
    s.add_argument("video"); s.add_argument("-o", "--out")
    add_plan_args(s)
    s.set_defaults(fn=cmd_autocut)

    s = sub.add_parser("plan", help="build an edit plan (plan.json) from a raw video")
    s.add_argument("video"); s.add_argument("-o", "--out")
    add_plan_args(s)
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("make", help="raw video -> plan -> render and/or CapCut draft")
    s.add_argument("video")
    add_plan_args(s)
    s.add_argument("--transcribe", action="store_true", help="run Whisper first")
    s.add_argument("--model", default="small"); s.add_argument("--language")
    s.add_argument("--plan-out")
    s.add_argument("--render", metavar="OUT.mp4")
    s.add_argument("--until", type=float, help="render only the first N seconds")
    s.add_argument("--capcut", metavar="DRAFT_NAME")
    s.add_argument("--quit-app", action="store_true")
    s.set_defaults(fn=cmd_make)

    s = sub.add_parser("auto", help="edit a CapCut project (or a folder of footage) "
                       "automatically, straight into CapCut")
    s.add_argument("target", nargs="?",
                   help="CapCut project name (loose match) or a folder of footage")
    s.add_argument("--folder", help="footage folder to use (with or instead of a project)")
    s.add_argument("--name", help="project to write when TARGET is a folder")
    s.add_argument("--style", choices=auto.STYLES, default="auto")
    s.add_argument("--aspect", default="auto", help=f"auto, {', '.join(auto.ASPECTS)}")
    s.add_argument("--max-seconds", type=float,
                   help="cap the length (montage default 60; talking keeps everything)")
    s.add_argument("--music", help="music file (default: the project's/folder's longest)")
    s.add_argument("--look", default="auto", help=f"auto, none, {', '.join(grade.PRESETS)}")
    s.add_argument("--captions", default="tiktok-raw",
                   help=f"caption style ({', '.join(styles.PRESETS)}) or 'none'")
    s.add_argument("--hook", help="hook card over the opening line")
    s.add_argument("--title", help="title card over the first seconds")
    s.add_argument("--no-broll", action="store_true", help="no cutaways on talking edits")
    s.add_argument("--no-match", action="store_true", help="skip shot matching")
    s.add_argument("--replace", action="store_true",
                   help="rebuild a project whose timeline already has clips (backed up)")
    s.add_argument("--quit-app", action="store_true",
                   help="quit CapCut if it is open (it saves on quit)")
    s.add_argument("--no-relaunch", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="decide and plan, write nothing")
    s.add_argument("--render", metavar="OUT.mp4", help="also render an MP4")
    s.set_defaults(fn=cmd_auto)

    s = sub.add_parser("render", help="render a plan to MP4 with ffmpeg")
    s.add_argument("plan"); s.add_argument("-o", "--out")
    s.add_argument("--until", type=float, help="preview: only the first N seconds")
    s.add_argument("--crf", type=int, default=18)
    s.add_argument("--preset", default="medium")
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("match", help="shot-match every clip toward one look (per-shot grades)")
    s.add_argument("plan"); s.add_argument("-o", "--out")
    s.add_argument("--look", choices=list(grade.PRESETS), help="also set the shared look")
    s.add_argument("--strength", type=float, default=0.75, help="0..1 how far to correct")
    s.add_argument("--target-luma", type=float, help="exposure target 0-255 (default: group mean)")
    s.set_defaults(fn=cmd_match)

    s = sub.add_parser("motion", help="add eased camera moves (keyframes) to every clip")
    s.add_argument("plan"); s.add_argument("-o", "--out")
    s.add_argument("--moves", default="push-in",
                   help=f"comma list, cycled over clips ({', '.join(motion.MOVES)})")
    s.add_argument("--intensity", type=float, default=1.0)
    s.add_argument("--fade-in", type=float, default=0.0, help="fade from black on clip 1")
    s.add_argument("--fade-out", type=float, default=0.0, help="fade to black on the last clip")
    s.add_argument("--letterbox", type=float, metavar="ASPECT", help="cinema bars (0 = off)")
    s.set_defaults(fn=cmd_motion)

    s = sub.add_parser("beats", help="tempo, beats and bar starts of a music file")
    s.add_argument("music"); s.add_argument("-o", "--out", help="write beats.json")
    s.set_defaults(fn=cmd_beats)

    s = sub.add_parser("montage", help="cut clips to the beat of a track -> plan.json")
    s.add_argument("clips", nargs="+", help="CLIP or CLIP@START-END (usable span, seconds); "
                   "repeat a clip to show it more often")
    s.add_argument("--music", required=True)
    s.add_argument("-o", "--out", default="montage.plan.json")
    s.add_argument("--bars", type=int, default=16, help="length in bars")
    s.add_argument("--start-bar", type=int, default=0, help="where in the song to start")
    s.add_argument("--pattern", default="2", help="shot lengths in beats, cycled, e.g. 4,2,2")
    s.add_argument("--look", choices=list(grade.PRESETS), help="colour grade")
    s.add_argument("--match", action="store_true", help="shot-match clips toward one look")
    s.add_argument("--width", type=int, default=1920)
    s.add_argument("--height", type=int, default=1080)
    s.add_argument("--fps", type=float, default=25.0)
    s.add_argument("--volume", type=float, default=1.0)
    s.add_argument("--fade-out", type=float, default=1.5)
    s.set_defaults(fn=cmd_montage)

    s = sub.add_parser("ambient", help="generate an original ambient sound bed")
    s.add_argument("out"); s.add_argument("--duration", type=float, default=60.0)
    s.add_argument("--mood", choices=list(ambient.MOODS), default="warm")
    s.set_defaults(fn=cmd_ambient)

    s = sub.add_parser("srt", help="export a plan's captions as .srt")
    s.add_argument("plan"); s.add_argument("-o", "--out")
    s.set_defaults(fn=cmd_srt)

    # ---- capcut file lane
    c = sub.add_parser("capcut", help="CapCut draft files (CapCut must be closed)")
    csub = c.add_subparsers(dest="ccmd", required=True)

    def writer(name, help_, fn):
        x = csub.add_parser(name, help=help_)
        x.add_argument("--quit-app", action="store_true",
                       help="quit CapCut if it is open (it saves on quit)")
        x.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
        x.add_argument("--no-relaunch", action="store_true")
        x.set_defaults(fn=fn)
        return x

    def target(x):
        x.add_argument("--track", choices=cc.ROLES, default="main")
        x.add_argument("--index", type=int, default=0)

    x = writer("build", "create a NEW draft from a plan (or --into-existing)", cmd_cc_build)
    x.add_argument("plan"); x.add_argument("--name", required=True)
    x.add_argument("--into-existing", action="store_true",
                   help="write the plan into the existing draft NAME instead")
    x.add_argument("--replace", action="store_true",
                   help="with --into-existing: replace a timeline that has clips (backed up)")
    csub.add_parser("ls", help="list drafts").set_defaults(fn=cmd_cc_ls)
    x = csub.add_parser("inspect", help="tracks and segments of a draft")
    x.add_argument("draft"); x.set_defaults(fn=cmd_cc_inspect)
    x = writer("add-overlay", "place a video/image above the main track", cmd_cc_overlay)
    x.add_argument("draft"); x.add_argument("media"); x.add_argument("--at", type=float, required=True)
    x.add_argument("--dur", type=float); x.add_argument("--src", type=float, default=0.0)
    x.add_argument("--layer", type=int, default=1)
    x.add_argument("--x", type=float, default=0.0); x.add_argument("--y", type=float, default=0.0)
    x.add_argument("--scale", type=float, default=1.0)
    x.add_argument("--unmute", action="store_true", help="keep the overlay's own audio")
    x = writer("add-text", "add a styled text segment", cmd_cc_text)
    x.add_argument("draft"); x.add_argument("text")
    x.add_argument("--at", type=float, required=True); x.add_argument("--dur", type=float, default=3.0)
    x.add_argument("--style", default="tiktok-raw", choices=list(styles.PRESETS))
    x = writer("add-captions", "add all captions from a plan to a draft", cmd_cc_captions)
    x.add_argument("draft"); x.add_argument("plan")
    x = writer("add-audio", "add music or a sound effect", cmd_cc_audio)
    x.add_argument("draft"); x.add_argument("media"); x.add_argument("--at", type=float, default=0.0)
    x.add_argument("--dur", type=float); x.add_argument("--volume", type=float, default=1.0)
    x.add_argument("--fade-in", type=float, default=0.0)
    x.add_argument("--fade-out", type=float, default=0.0)
    x.add_argument("--loop", action="store_true", help="loop to the end of the main track")
    x = writer("transform", "set scale/position/rotation/opacity", cmd_cc_transform)
    x.add_argument("draft"); target(x)
    for f in ("--scale", "--x", "--y", "--rotate", "--opacity"):
        x.add_argument(f, type=float)
    x = writer("keyframe", "add a motion keyframe (timeline seconds)", cmd_cc_keyframe)
    x.add_argument("draft"); target(x); x.add_argument("--at", type=float, required=True)
    for f in ("--scale", "--x", "--y", "--rotate", "--opacity", "--volume"):
        x.add_argument(f, type=float)
    x.add_argument("--curve", default="Line")
    x = writer("clear-keyframes", "remove a segment's keyframes", cmd_cc_clear_kf)
    x.add_argument("draft"); target(x)
    x = writer("grade", "colour-grade clips with CapCut's Adjust sliders", cmd_cc_grade)
    x.add_argument("draft"); x.add_argument("--preset", choices=list(grade.PRESETS))
    x.add_argument("--tracks", default="main", help="roles to grade, e.g. main,overlay")
    for sl in grade.SLIDERS:
        x.add_argument(f"--{sl.replace('_', '-')}", dest=sl, type=float,
                       help="-1..1 (CapCut slider = value x 50)")
    x = writer("restyle", "apply a plan's per-shot grades, moves and bars to a draft",
               cmd_cc_restyle)
    x.add_argument("draft"); x.add_argument("plan")
    x = writer("remove", "delete a segment", cmd_cc_remove)
    x.add_argument("draft"); target(x)
    x = csub.add_parser("backups", help="list a draft's backups")
    x.add_argument("draft"); x.set_defaults(fn=cmd_cc_backups)
    x = csub.add_parser("restore", help="roll a draft back to a backup")
    x.add_argument("draft"); x.add_argument("--backup", help="backup id (default: latest)")
    x.add_argument("--quit-app", action="store_true"); x.set_defaults(fn=cmd_cc_restore)

    # ---- live lane
    lv = sub.add_parser("live", help="drive the open CapCut editor (needs --extra live)")
    lv.add_argument("action", choices=LIVE_ACTIONS)
    lv.add_argument("args", nargs="*")
    lv.add_argument("--fps", type=float, default=30.0)
    lv.add_argument("--times", type=int, default=1)
    lv.add_argument("--to", help="export folder (default ~/Downloads)")
    lv.add_argument("--timeout", type=float, default=900)
    lv.set_defaults(fn=cmd_live)
    return p


def main(argv: list[str] | None = None) -> None:
    a = parser().parse_args(argv)
    try:
        a.fn(a)
    except (DraftError, app.AppRunningError, media.MediaError, ValueError, KeyError) as e:
        sys.exit(f"error: {e}")
    except Exception as e:
        if type(e).__name__ in ("LiveError", "RenderError", "TranscribeError"):
            sys.exit(f"error: {e}")
        raise


if __name__ == "__main__":
    main()
