"""Timeline words -> caption cues."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import styles
from .timeline import Caption

DEFAULT_CORRECTIONS = {
    "claud": "Claude", "clod": "Claude", "clawed": "Claude", "anthropic": "Anthropic",
    "chatgpt": "ChatGPT", "ai": "AI", "mcp": "MCP", "capcut": "CapCut", "youtube": "YouTube",
    "tiktok": "TikTok", "api": "API",
}

_BREAK = re.compile(r"[.!?,;:—–]$")


def load_corrections(path: str | Path | None) -> dict[str, str]:
    fixes = dict(DEFAULT_CORRECTIONS)
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        fixes.update({k.lower(): v for k, v in data.get("auto", data).items()})
    return fixes


def correct(word: str, fixes: dict[str, str]) -> str:
    m = re.match(r"^(\W*)([\w']+)(\W*)$", word)
    if not m:
        return word
    pre, core, post = m.groups()
    return pre + fixes.get(core.lower(), core) + post


def phrases(words: list[dict], max_words: int = 4, max_chars: int = 20,
            gap_break: float = 0.6) -> list[list[dict]]:
    """Group words into caption lines, breaking on punctuation, pauses, or size."""
    out: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            text_len = len(" ".join(x["word"] for x in cur + [w]))
            if (len(cur) >= max_words or text_len > max_chars
                    or w["start"] - cur[-1]["end"] > gap_break):
                out.append(cur)
                cur = []
        cur.append(w)
        if _BREAK.search(w["word"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def build(words: list[dict], style: str = "tiktok-raw", corrections: dict | None = None,
          hold: bool = True, max_hold_gap: float = 1.0, min_sliver: float = 0.2,
          strip_punct: bool = True) -> list[Caption]:
    """Caption cues for a timeline. With `hold`, each line stays up until the
    next begins (no flicker), unless the pause is longer than max_hold_gap."""
    st = styles.get(style)
    fixes = corrections if corrections is not None else DEFAULT_CORRECTIONS
    fixed = [{**w, "word": correct(w["word"], fixes)} for w in words]
    groups = phrases(fixed, st.max_words, st.max_chars)
    # Clip-boundary false-start tails ("what?" for 0.1s) read as glitches. In a
    # one-word-per-cue style every cue is a single word, so that test would
    # eat every short word ("the", "a"): there, keep them all.
    if st.max_words > 1:
        groups = [g for g in groups
                  if not (len(g) == 1 and g[0]["end"] - g[0]["start"] < min_sliver)]
    cues = []
    for i, g in enumerate(groups):
        text = " ".join(w["word"] for w in g)
        if strip_punct:
            text = re.sub(r"[.,;:]+(?=\s|$)", "", text).strip()
        start, end = g[0]["start"], g[-1]["end"]
        if hold and i + 1 < len(groups):
            nxt = groups[i + 1][0]["start"]
            if nxt - end <= max_hold_gap:
                end = nxt
        cues.append(Caption(text=text, start=round(start, 3), end=round(end, 3), style=style))
    return cues


def hook(text: str, words: list[dict], end: float | None = None,
         style: str = "hook") -> Caption:
    """Hook card over the opening line: until `end`, else the first sentence break."""
    if end is None:
        end = next((w["end"] for w in words if _BREAK.search(w["word"])
                    and w["word"][-1] in ".!?"), None) or min(3.0, words[-1]["end"] if words else 3.0)
    return Caption(text=text, start=0.0, end=round(end, 3), style=style, role="hook")


def to_srt(cues: list[Caption], path: str | Path) -> Path:
    def ts(t: float) -> str:
        ms = round(t * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    body = "\n".join(f"{i}\n{ts(c.start)} --> {ts(c.end)}\n{c.text}\n"
                     for i, c in enumerate((c for c in cues if c.role == "caption"), 1))
    path = Path(path)
    path.write_text(body, encoding="utf-8")
    return path
