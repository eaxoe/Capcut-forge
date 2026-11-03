"""Caption rasterising with Pillow (this ffmpeg build has no drawtext/libass,
and PIL gives exact control over weight, stroke and box anyway)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..styles import CaptionStyle


@lru_cache(maxsize=32)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, stroke: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) + 2 * stroke > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def draw_caption(canvas: Image.Image, text: str, style: CaptionStyle) -> None:
    """Draw one caption onto a full-frame RGBA canvas, in place."""
    W, H = canvas.size
    k = W / 1080
    font = _font(style.font, max(8, round(style.size * k)))
    stroke = round(style.stroke_width * k)
    pad_x, pad_y = round(style.pad_x * k), round(style.pad_y * k)
    d = ImageDraw.Draw(canvas)
    text = text.upper() if style.uppercase else text
    lines = _wrap(d, text, font, round(style.max_width * k) - 2 * pad_x, stroke)
    spacing = round(style.size * k * 0.18)
    block = "\n".join(lines)
    # Measure the real ink extents so the box hugs the text, not the font metrics.
    l, t, r, b = d.multiline_textbbox((0, 0), block, font=font, spacing=spacing,
                                      align="center", stroke_width=stroke)
    w, h = r - l, b - t
    cx, cy = W / 2, style.y_frac * H
    x, y = cx - w / 2 - l, cy - h / 2 - t
    if style.box_color:
        d.rounded_rectangle((cx - w / 2 - pad_x, cy - h / 2 - pad_y,
                             cx + w / 2 + pad_x, cy + h / 2 + pad_y),
                            radius=round(style.box_radius * k), fill=style.box_color)
    d.multiline_text((x, y), block, font=font, fill=style.color, spacing=spacing,
                     align="center", stroke_width=stroke, stroke_fill=style.stroke_color)


def frame(size: tuple[int, int], cues: list[tuple[str, CaptionStyle]], out: Path) -> Path:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for text, style in cues:
        draw_caption(img, text, style)
    img.save(out, optimize=False, compress_level=1)
    return out
