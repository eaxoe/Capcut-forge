"""Word-level transcription, run locally (nothing leaves the machine).

Needs the optional extra:  uv sync --extra transcribe
The Whisper model downloads from Hugging Face on first use, then is cached.
"""
from __future__ import annotations

import json
from pathlib import Path

from .timeline import Clip


class TranscribeError(RuntimeError):
    pass


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


_MODELS: dict[str, object] = {}


def _pcm16k(path: str | Path):
    """The audio as 16 kHz mono float32, decoded by ffmpeg. faster-whisper's
    own decoder goes through PyAV, whose API keeps changing under it (PyAV 19
    broke faster-whisper 1.2), so it never gets a file path."""
    import subprocess

    import numpy as np

    from . import media
    out = subprocess.run([media.require("ffmpeg"), "-v", "error", "-i", str(path), "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True)
    if out.returncode != 0:
        raise TranscribeError(f"could not read audio from {path}: "
                              f"{out.stderr.decode(errors='ignore').strip()}")
    return np.frombuffer(out.stdout, dtype=np.float32)


def transcribe(path: str | Path, model: str = "small", language: str | None = None) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise TranscribeError("transcription needs the extra: uv sync --extra transcribe") from None
    if model not in _MODELS:                  # loading a model takes seconds; keep it
        _MODELS[model] = WhisperModel(model, device="auto", compute_type="int8")
    whisper = _MODELS[model]
    segs, info = whisper.transcribe(_pcm16k(path), language=language, word_timestamps=True,
                                    vad_filter=True)
    words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
              "p": round(w.probability, 3)}
             for s in segs for w in (s.words or []) if w.word.strip()]
    return {"source": str(Path(path).resolve()), "language": info.language,
            "model": model, "words": words}


def save(transcript: dict, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cached(path: str | Path, model: str = "small", language: str | None = None,
           log=print) -> dict:
    """transcribe(), remembered per file version under capcut-forge's own folder,
    so re-editing the same footage never waits on Whisper twice and nothing
    is written next to the user's media."""
    import hashlib

    from .capcut.store import forge_home
    src = Path(path).expanduser().resolve()
    st = src.stat()
    tag = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}|{model}|{language}".encode())
    out = forge_home() / "transcripts" / f"{src.stem[:40]}-{tag.hexdigest()[:12]}.json"
    if out.exists():
        return load(out)
    log(f"transcribing {src.name} (Whisper {model}, runs locally)")
    t = transcribe(src, model, language)
    out.parent.mkdir(parents=True, exist_ok=True)
    save(t, out)
    return t


def remap(words: list[dict], clips: list[Clip], source: str | None = None) -> list[dict]:
    """Source-time words -> output-timeline words, through the cut list.
    Words that straddle a cut are clamped; words in removed spans vanish.
    This is what keeps captions on-beat with zero manual nudging."""
    out, t0 = [], 0.0
    for c in clips:
        if source is None or Path(c.source).name == Path(source).name:
            for w in words:
                if w["end"] <= c.start or w["start"] >= c.end:
                    continue
                s = max(w["start"], c.start) - c.start + t0
                e = min(w["end"], c.end) - c.start + t0
                if e - s > 0.02:
                    out.append({**w, "start": round(s, 3), "end": round(e, 3)})
        t0 += c.duration
    return out
