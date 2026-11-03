"""Timeline -> finished MP4 with ffmpeg alone. No CapCut required.

Graph: every main clip is trimmed, cover-fitted (plus punch-in zoom) and
concatenated; overlays are composited at their times; all captions are baked
into ONE transparent image stream (one PNG per change point, so overlapping
hook + caption composite correctly); music/SFX are trimmed, faded, delayed and
mixed over the dialogue.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import grade, media, styles
from ..timeline import Timeline
from . import text as textr


class RenderError(RuntimeError):
    pass


def _caption_stream(tl: Timeline, workdir: Path, end: float) -> Path | None:
    """Write PNG frames + a concat-demuxer list covering [0, end)."""
    cues = [c for c in tl.captions if c.start < end]
    if not cues:
        return None
    points = sorted({0.0, end, *(min(c.start, end) for c in cues),
                     *(min(c.end, end) for c in cues)})
    blank = textr.frame((tl.width, tl.height), [], workdir / "cap-blank.png")
    lines, cache = [], {}
    for i, (a, b) in enumerate(zip(points, points[1:])):
        if b - a <= 1e-4:
            continue
        mid = (a + b) / 2
        active = sorted((c for c in cues if c.start <= mid < c.end),
                        key=lambda c: c.role == "hook")   # hook card drawn on top
        key = tuple((c.text, c.style) for c in active)
        if not key:
            img = blank
        elif key in cache:
            img = cache[key]
        else:
            img = cache[key] = textr.frame(
                (tl.width, tl.height), [(c.text, styles.get(c.style)) for c in active],
                workdir / f"cap-{i:05d}.png")
        lines += [f"file '{img.name}'", f"duration {b - a:.4f}"]
    lines.append(f"file '{blank.name}'")    # concat demuxer drops the last duration
    lst = workdir / "captions.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst


def build_command(tl: Timeline, out: Path, workdir: Path, until: float | None = None,
                  crf: int = 18, preset: str = "medium") -> list[str]:
    W, H, fps = tl.width, tl.height, tl.fps
    end = min(tl.duration, until) if until else tl.duration
    inputs: list[list[str]] = []
    index: dict[tuple, int] = {}

    def inp(path: str, *pre: str) -> int:
        key = (path, pre)
        if key not in index:
            index[key] = len(inputs)
            inputs.append([*pre, "-i", path])
        return index[key]

    infos = {}

    def info(path: str) -> media.MediaInfo:
        if path not in infos:
            infos[path] = media.probe(path)
        return infos[path]

    g: list[str] = []
    # ---- main track
    vlabels, alabels, t = [], [], 0.0
    for n, c in enumerate(tl.clips):
        if t >= end:
            break
        dur = min(c.duration, end - t)
        mi = info(c.source)
        if mi.is_image:
            k = inp(c.source, "-loop", "1", "-framerate", str(fps))
            g.append(f"[{k}:v]trim=duration={dur:.4f},setpts=PTS-STARTPTS,")
        else:
            k = inp(c.source)
            g.append(f"[{k}:v]trim=start={c.start:.4f}:duration={dur:.4f},setpts=PTS-STARTPTS,")
        zw, zh = round(W * c.zoom / 2) * 2, round(H * c.zoom / 2) * 2
        look = grade.ffmpeg_filters(tl.clip_grade(c))   # per-shot: look + matching
        # reframe: moving the picture right/up moves the crop window left/down
        cx = f"max(0\\,min(iw-{W}\\,(iw-{W})/2-({c.x})*{W}/2))"
        cy = f"max(0\\,min(ih-{H}\\,(ih-{H})/2+({c.y})*{H}/2))"
        g[-1] += (f"scale={zw}:{zh}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H}:{cx}:{cy},setsar=1,fps={fps},"
                  f"{look + ',' if look else ''}format=yuv420p[v{n}];")
        if mi.has_audio and not mi.is_image and c.volume > 0:
            g.append(f"[{k}:a]atrim=start={c.start:.4f}:duration={dur:.4f},"
                     f"asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo"
                     f"{f',volume={c.volume}' if c.volume != 1 else ''}[a{n}];")
        else:
            g.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.4f}[a{n}];")
        vlabels.append(f"[v{n}]")
        alabels.append(f"[a{n}]")
        t += dur
    if not vlabels:
        raise RenderError("nothing to render")
    pairs = "".join(v + a for v, a in zip(vlabels, alabels))
    g.append(f"{pairs}concat=n={len(vlabels)}:v=1:a=1[cut][dialog];")
    vcur = "[cut]"
    if tl.letterbox:
        bar = max(0, round((H - W / tl.letterbox) / 2))
        g.append(f"[cut]drawbox=y=0:w=iw:h={bar}:c=black:t=fill,"
                 f"drawbox=y=ih-{bar}:w=iw:h={bar}:c=black:t=fill[boxed];")
        vcur = "[boxed]"

    # ---- overlays
    for n, ov in enumerate(o for o in tl.overlays if o.at < end):
        mi = info(ov.source)
        length = ov.duration if ov.duration is not None else (mi.duration - ov.src_in) or 3.0
        if mi.is_image:
            k = inp(ov.source, "-loop", "1", "-framerate", str(fps))
            src = f"[{k}:v]trim=duration={length:.4f},"
        else:
            k = inp(ov.source)
            src = f"[{k}:v]trim=start={ov.src_in:.4f}:duration={length:.4f},"
        # scale 1.0 = fit inside the canvas, like CapCut's default placement
        sw, sh = round(W * ov.scale / 2) * 2, round(H * ov.scale / 2) * 2
        g.append(f"{src}setpts=PTS-STARTPTS+{ov.at:.4f}/TB,"
                 f"scale={sw}:{sh}:force_original_aspect_ratio=decrease,"
                 f"format=rgba[ov{n}];")
        x = f"(W-w)/2+({ov.x})*W/2"
        y = f"(H-h)/2-({ov.y})*H/2"
        g.append(f"{vcur}[ov{n}]overlay=x={x}:y={y}:eof_action=pass:"
                 f"enable='between(t,{ov.at:.4f},{ov.at + length:.4f})'[vo{n}];")
        vcur = f"[vo{n}]"

    # ---- captions
    caps = _caption_stream(tl, workdir, end)
    if caps:
        k = inp(str(caps), "-f", "concat", "-safe", "0")
        g.append(f"[{k}:v]fps={fps},format=rgba[caps];")
        g.append(f"{vcur}[caps]overlay=0:0:eof_action=pass:format=auto[vcap];")
        vcur = "[vcap]"

    # ---- music / sfx
    mix = ["[dialog]"]
    for n, a in enumerate(x for x in tl.audio if x.at < end):
        mi = info(a.source)
        want = (a.duration if a.duration is not None
                else (end - a.at if a.loop else mi.duration - a.src_in))
        want = max(0.0, min(want, end - a.at))
        k = inp(a.source, "-stream_loop", "-1") if a.loop else inp(a.source)
        chain = (f"[{k}:a]atrim=start={a.src_in:.4f}:duration={want:.4f},asetpts=PTS-STARTPTS,"
                 f"aresample=48000,aformat=channel_layouts=stereo,volume={a.volume}")
        if a.fade_in:
            chain += f",afade=t=in:d={a.fade_in}"
        if a.fade_out and want > a.fade_out:
            chain += f",afade=t=out:st={want - a.fade_out:.4f}:d={a.fade_out}"
        ms = round(a.at * 1000)
        chain += f",adelay={ms}|{ms}[m{n}];"
        g.append(chain)
        mix.append(f"[m{n}]")
    if len(mix) > 1:
        g.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[aout];")
        acur = "[aout]"
    else:
        acur = "[dialog]"

    graph = workdir / "graph.txt"
    graph.write_text("".join(g).rstrip(";"), encoding="utf-8")
    cmd = [media.require("ffmpeg"), "-hide_banner", "-y", "-loglevel", "error", "-stats"]
    for i in inputs:
        cmd += i
    cmd += ["-/filter_complex", str(graph), "-map", vcur, "-map", acur,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-t", f"{end:.4f}",
            "-movflags", "+faststart", str(out)]
    return cmd


def render(tl: Timeline, out: str | Path, until: float | None = None, crf: int = 18,
           preset: str = "medium", keep_workdir: bool = False) -> Path:
    problems = tl.validate()
    if problems:
        raise RenderError("timeline is not renderable:\n  " + "\n  ".join(problems))
    out = Path(out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="capcut-forge-"))
    cmd = build_command(tl, out, workdir, until, crf, preset)
    res = subprocess.run(cmd, cwd=workdir)
    if res.returncode != 0:
        raise RenderError(f"ffmpeg failed (exit {res.returncode}); inputs and graph "
                          f"kept in {workdir}")
    if not keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    return out
