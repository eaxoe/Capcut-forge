from PIL import Image

from capcut_forge import media, styles
from capcut_forge.render import text as textr
from capcut_forge.render.ffmpeg import render
from capcut_forge.timeline import AudioTrack, Caption, Clip, Overlay, Timeline


def test_render_full_timeline(media_dir, tmp_path):
    v = str(media_dir / "talk.mp4")
    tl = Timeline(
        clips=[Clip(v, 0.0, 1.4), Clip(v, 2.5, 4.0, zoom=1.2)],
        overlays=[Overlay(str(media_dir / "badge.png"), at=0.3, duration=1.0, y=0.5,
                          scale=0.3)],
        captions=[Caption("one two three", 0.0, 1.0), Caption("hook", 0.0, 2.0,
                                                              style="hook", role="hook"),
                  Caption("four five", 1.0, 2.9)],
        audio=[AudioTrack(str(media_dir / "music.wav"), volume=0.2, fade_in=0.2,
                          fade_out=0.5, loop=True)],
        width=540, height=960,
    )
    out = render(tl, tmp_path / "out.mp4", preset="ultrafast")
    info = media.probe(out)
    assert info.width == 540 and info.height == 960 and info.has_audio
    assert abs(info.duration - 2.9) < 0.15


def test_preview_until(media_dir, tmp_path):
    v = str(media_dir / "talk.mp4")
    tl = Timeline(clips=[Clip(v, 0.0, 4.0)], width=320, height=568)
    out = render(tl, tmp_path / "p.mp4", until=1.0, preset="ultrafast")
    assert abs(media.probe(out).duration - 1.0) < 0.15


def test_caption_box_hugs_text(tmp_path):
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    textr.draw_caption(img, "short", styles.get("explainer"))
    l, t, r, b = img.getbbox()
    assert r - l < 400 and abs((t + b) / 2 - 960) < 10


def test_long_caption_wraps(tmp_path):
    img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    textr.draw_caption(img, "this is a very long caption that must wrap onto lines",
                       styles.get("tiktok-raw"))
    l, t, r, b = img.getbbox()
    assert r - l <= 960 and b - t > 60


def test_render_with_grade_and_ambient(media_dir, tmp_path):
    from capcut_forge import ambient, grade
    v = str(media_dir / "talk.mp4")
    bed = ambient.generate(tmp_path / "bed.wav", 3.0, "dreamy")
    assert abs(media.probe(bed).duration - 3.0) < 0.05
    for name in grade.PRESETS:
        tl = Timeline(clips=[Clip(v, 0.0, 1.0)], width=320, height=568,
                      grade=grade.resolve(name), audio=[AudioTrack(str(bed), volume=0.3)])
        out = render(tl, tmp_path / f"{name}.mp4", preset="ultrafast")
        assert media.probe(out).has_audio


def test_grade_rejects_unknown_slider():
    import pytest
    from capcut_forge import grade
    with pytest.raises(ValueError):
        grade.resolve("soft-cinematic", glow=0.3)
