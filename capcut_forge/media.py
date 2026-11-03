"""ffprobe / ffmpeg helpers. Every call passes an argument list, never a shell string."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".heic", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".3gp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff"}


def kind(path: str | Path) -> str | None:
    """'video' | 'image' | 'audio' by extension, None for anything else."""
    ext = Path(path).suffix.lower()
    return ("video" if ext in VIDEO_EXTS else "image" if ext in IMAGE_EXTS
            else "audio" if ext in AUDIO_EXTS else None)


class MediaError(RuntimeError):
    pass


class AccessError(MediaError):
    """The OS won't let us read a file (macOS privacy, Windows security)."""


# macOS asks per app before it lets anything read these home folders.
_PROTECTED = {"Downloads": "Downloads Folder", "Desktop": "Desktop Folder",
              "Documents": "Documents Folder"}


def access_hint(path: str | Path, count: int = 1) -> str:
    """What to do when the OS blocks reading footage, in the user's terms."""
    path = Path(path)
    what = f"{path}" + (f" (and {count - 1} more file(s))" if count > 1 else "")
    if sys.platform == "darwin":
        try:
            top = path.resolve().relative_to(Path.home()).parts[0]
        except (ValueError, IndexError):
            top = ""
        switch = (f"turn on \"{_PROTECTED[top]}\"" if top in _PROTECTED
                  else "turn on access to the drive or folder it's on (or use Full Disk Access)")
        # Claude Code runs as its own "claude" app (launched through a helper),
        # so macOS keeps its permissions apart from the "Claude" app's.
        return (f"macOS is blocking access to {what}. Open System Settings > Privacy & "
                "Security > Files and Folders, find \"claude\" (Claude Code's engine, "
                "listed separately from the \"Claude\" app; or the terminal app you run "
                f"capcut-forge from), {switch}, then quit Claude completely (Cmd+Q) and "
                "reopen it. If \"claude\" isn't listed, add it under Full Disk Access "
                "instead. Or move the footage to a folder it can already read.")
    if sys.platform == "win32":
        return (f"Windows won't let capcut-forge read {what}. Close any program that has "
                "it open; if Windows Security's \"Controlled folder access\" is on, allow "
                "uv/Python through it.")
    return f"no permission to read {what}"


def check_readable(paths: list[str | Path]) -> None:
    """Open every file before any work starts, so a privacy block is one clear
    message up front instead of a cryptic failure halfway through an edit.
    (On macOS a blocked file still shows up as existing; only reading fails.)"""
    blocked = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                f.read(1)
        except PermissionError:
            blocked.append(p)
    if blocked:
        raise AccessError(access_hint(blocked[0], len(blocked)))


def require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise MediaError(f"{tool} not found on PATH (brew install ffmpeg)")
    return path


@dataclass
class MediaInfo:
    path: Path
    duration: float          # seconds; 0 for still images
    width: int | None
    height: int | None
    fps: float | None
    has_video: bool
    has_audio: bool

    @property
    def is_image(self) -> bool:
        return self.path.suffix.lower() in IMAGE_EXTS


def _rotation(stream: dict) -> int:
    """Display rotation in degrees. Phones record portrait video as landscape
    pixels plus a rotation flag, so width/height must be swapped for 90/270."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            return int(float(sd["rotation"])) % 360
    return int(float((stream.get("tags") or {}).get("rotate", 0))) % 360


_PROBES: dict[tuple, "MediaInfo"] = {}


def probe(path: str | Path) -> MediaInfo:
    """ffprobe, cached per (path, size, mtime): the planner, builder and
    renderer all probe the same files, and each call is a subprocess."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"media not found: {path}")
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key in _PROBES:
        info = _PROBES[key]
        return MediaInfo(path, info.duration, info.width, info.height, info.fps,
                         info.has_video, info.has_audio)
    out = subprocess.run(
        [require("ffprobe"), "-v", "error", "-show_entries",
         "stream=codec_type,width,height,duration,avg_frame_rate:"
         "stream_tags=rotate:stream_side_data=rotation:"
         "stream_disposition=attached_pic:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        if "Operation not permitted" in out.stderr or "Permission denied" in out.stderr:
            raise AccessError(access_hint(path))
        raise MediaError(f"ffprobe failed on {path}: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    # Cover art in an mp3/m4a shows up as a one-frame "video" stream; it isn't one.
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = float(data.get("format", {}).get("duration") or
                (video or {}).get("duration") or 0.0)
    fps = None
    if video and video.get("avg_frame_rate", "0/0") != "0/0":
        num, den = video["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else None
    w, h = (int(video["width"]), int(video["height"])) if video else (None, None)
    if video and _rotation(video) in (90, 270):
        w, h = h, w
    info = MediaInfo(path, dur, w, h, fps, video is not None, has_audio)
    if info.is_image:
        info.duration = 0.0
    _PROBES[key] = info
    return info


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)")


def detect_silences(path: str | Path, noise_db: float = -35.0,
                    min_silence: float = 0.4) -> list[tuple[float, float]]:
    """Silent spans as (start, end) seconds, via ffmpeg's silencedetect."""
    info = probe(path)
    if not info.has_audio:
        raise MediaError(f"{path} has no audio track to detect silence on")
    out = subprocess.run(
        [require("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
         "-vn", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    spans, start = [], None
    for line in out.stderr.splitlines():
        if m := _SIL_START.search(line):
            start = max(0.0, float(m.group(1)))
        elif (m := _SIL_END.search(line)) and start is not None:
            spans.append((start, float(m.group(1))))
            start = None
    if start is not None:                      # file ends in silence
        spans.append((start, info.duration))
    return spans


def extract_frame(src: str | Path, at: float, out: str | Path, width: int = 480) -> None:
    subprocess.run(
        [require("ffmpeg"), "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(src),
         "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)], check=False)
