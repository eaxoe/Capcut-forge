from capcut_forge import captions, cuts, media, transcribe
from capcut_forge.plan import PlanOptions, plan
from capcut_forge.timeline import Clip, Timeline


def test_silence_cut_removes_the_gap(media_dir):
    path = media_dir / "talk.mp4"
    sil = media.detect_silences(path, min_silence=0.4)
    assert any(1.4 < s < 1.7 and 2.3 < e < 2.6 for s, e in sil)
    ranges = cuts.from_silences(media.probe(path).duration, sil, pad=0.05)
    assert len(ranges) == 2
    assert ranges[0][1] < 1.7 and ranges[1][0] > 2.3


def test_word_cut_drops_fillers_and_long_pauses(words):
    ranges = cuts.from_words(words, 4.0, max_gap=0.5, pad=0.0, min_keep=0.0)
    kept = [(s, e) for s, e in ranges]
    # "um" (0.25-0.5) removed, pause 1.5-2.6 removed
    assert not any(s <= 0.3 and e >= 0.45 for s, e in kept)
    assert not any(s < 2.0 < e for s, e in kept)


def test_retakes_keep_last_take():
    ws = [{"word": w, "start": i, "end": i + 0.5}
          for i, w in enumerate(["so", "I", "so", "I", "went", "home"])]
    kept = [w["word"] for w in cuts._drop_retakes(ws)]
    assert kept == ["so", "I", "went", "home"]


def test_cuts_roundtrip(tmp_path, media_dir):
    src = media_dir / "talk.mp4"
    out = cuts.save([(0.0, 1.0), (2.5, 4.0)], src, tmp_path / "cuts.json")
    clips = cuts.load(out)
    assert [(c.start, c.end) for c in clips] == [(0.0, 1.0), (2.5, 4.0)]
    assert clips[0].source == str(src.resolve())


def test_remap_through_cuts(words):
    clips = [Clip("a.mp4", 0.5, 1.5), Clip("a.mp4", 2.5, 4.0)]
    out = transcribe.remap(words, clips)
    texts = [w["word"] for w in out]
    assert "So" not in texts and "It" in texts
    it = next(w for w in out if w["word"] == "It")
    assert abs(it["start"] - (1.0 + 0.1)) < 1e-6     # 1.0s of clip 1, then 0.1s in


def test_captions_phrase_hold_and_fix(words):
    cues = captions.build(words, "tiktok-raw")
    assert any("Claude" in c.text for c in cues)
    assert all(c.end > c.start for c in cues)
    # holds until next cue when the gap is short
    for a, b in zip(cues, cues[1:]):
        if b.start - a.end < 1e-9:
            continue
        assert b.start - a.end > 0 or a.end == b.start


def test_hook_ends_on_sentence(words):
    h = captions.hook("watch this", words)
    assert h.role == "hook" and h.end == 1.5


def test_srt(tmp_path, words):
    p = captions.to_srt(captions.build(words), tmp_path / "x.srt")
    assert "-->" in p.read_text(encoding="utf-8")


def test_plan_end_to_end(media_dir, tmp_path, words):
    tl, _ = plan(media_dir / "talk.mp4",
                 PlanOptions(cut="words", punch_in=1.1, hook_text="hook!",
                             music=str(media_dir / "music.wav")),
                 transcript={"words": words}, log=lambda *_: None)
    assert tl.validate() == []
    assert len(tl.clips) >= 2 and tl.clips[1].zoom == 1.1
    assert tl.captions[0].role == "hook"
    assert tl.audio[0].loop
    again = Timeline.load(tl.save(tmp_path / "p.json"))
    assert again.to_dict() == tl.to_dict()


def test_match_pulls_shots_together():
    from capcut_forge.match import ShotStats, offsets
    dark_warm = ShotStats(luma=25, cast=10, tint=0, sat=6)
    bright_cool = ShotStats(luma=95, cast=-11, tint=0, sat=8)
    a, b = offsets([dark_warm, bright_cool])
    assert a["brightness"] > 0 and a["shadow"] > 0 and a["temperature"] < 0
    assert b["brightness"] < 0 and b["temperature"] > 0 and b["saturation"] < 0


def test_motion_presets_are_eased_and_in_bounds():
    from capcut_forge import motion
    for name in motion.MOVES:
        keys = motion.preset(name, 6.0, 25)
        for prop, pts in keys.items():
            ts = [t for t, _ in pts]
            assert ts == sorted(ts) and ts[0] == 0 and ts[-1] <= 6.0 - 1 / 25 + 1e-9
        # any sideways move is covered by enough zoom that no edge shows
        sc = min(v for _, v in keys["scale"])
        for prop in ("x", "y"):
            for _, v in keys.get(prop, []):
                assert abs(v) < sc - 1
    f = motion.fade(motion.preset("push-in", 4.0), 4.0, fade_in=0.8, fade_out=1.0)
    assert f["opacity"][0][1] == 0.0 and f["opacity"][-1][1] == 0.0
