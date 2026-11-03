"""Caption looks, defined once and used by both backends.

Pixel values are for a 1080-wide canvas and scale with the real width. The
CapCut numbers (`capcut_*`) are CapCut's own units and are approximate —
tune them with `capcut-forge capcut add-text` + a look in the app.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


@dataclass(frozen=True)
class CaptionStyle:
    name: str
    font: str = str(FONT_DIR / "Inter-Bold.otf")
    size: int = 42                   # px at 1080 wide
    color: str = "#ffffff"
    stroke_width: int = 0            # px
    stroke_color: str = "#000000"
    box_color: str | None = None     # None = no box
    box_radius: int = 0
    pad_x: int = 0
    pad_y: int = 0
    max_width: int = 940             # px, wraps beyond this
    y_frac: float = 0.78             # vertical CENTER as a fraction of height (0 = top)
    uppercase: bool = False
    max_words: int = 4               # phrasing for auto-captions
    max_chars: int = 20
    # CapCut units (approximate):
    capcut_size: float = 9.0
    capcut_stroke: float = 0.08
    capcut_radius: float = 0.3
    capcut_font_path: str | None = None   # filled in by the CapCut builder

    @property
    def capcut_y(self) -> float:
        """CapCut transform y: canvas-normalised, +1 = top edge, 0 = centre."""
        return round((0.5 - self.y_frac) * 2, 4)

    def with_(self, **kw) -> "CaptionStyle":
        return replace(self, **kw)


PRESETS: dict[str, CaptionStyle] = {
    # Line captions low under the face: white, black stroke, no box.
    "tiktok-raw": CaptionStyle(
        name="tiktok-raw", size=42, stroke_width=4, y_frac=1500 / 1920,
        capcut_size=9.0, capcut_stroke=0.12),
    # Hook card pinned top: black on a white rounded box.
    "hook": CaptionStyle(
        name="hook", size=64, color="#000000", box_color="#ffffff", box_radius=22,
        pad_x=32, pad_y=20, y_frac=0.16, max_words=8, max_chars=40,
        capcut_size=13.0, capcut_radius=0.35),
    # Centred explainer captions: white on a black box.
    "explainer": CaptionStyle(
        name="explainer", size=49, box_color="#000000", box_radius=14, pad_x=24,
        pad_y=14, y_frac=0.5, capcut_size=10.0, capcut_radius=0.25),
    # Big centred single words, the "punchy" short-form look.
    "punch": CaptionStyle(
        name="punch", font=str(FONT_DIR / "Inter-Black.otf"), size=86, stroke_width=7,
        y_frac=0.62, uppercase=True, max_words=1, max_chars=14,
        capcut_size=18.0, capcut_stroke=0.14),
}


def get(name: str) -> CaptionStyle:
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(f"unknown caption style {name!r} (have: {', '.join(PRESETS)})") from None
