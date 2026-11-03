import json
import shutil
import time

import pytest

from capcut_forge import auto, beats, cuts, media, transcribe
from capcut_forge.capcut import build as cc
from capcut_forge.capcut import schema
from capcut_forge.capcut.store import DraftError
from capcut_forge.timeline import Clip, Timeline

from .conftest import ff


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPCUT_FORGE_HOME", str(tmp_path / "home"))


@pytest.fixture(scope="session")
def footage(media_dir):
    """A folder like a user's: two b-roll clips (one portrait, no audio), a
    still, a beat-heavy music track and a file that isn't media."""
    d = media_dir / "footage"
    d.mkdir()
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", str(d / "a-wide.mp4"))
    ff("-f", "lavfi", "-i", "testsrc=size=360x640:rate=25:duration=5", "-c:v", "libx264",
       "-pix_fmt", "yuv420p", str(d / "b-tall.mp4"))
    shutil.copy(media_dir / "badge.png", d / "c-still.png")
    # a kick every 0.5s = 120 BPM
    ff("-f", "lavfi", "-i", "aevalsrc='0.9*sin(2*PI*55*t)*exp(-25*mod(t,0.5))':s=22050:d=24",
       str(d / "song.wav"))
    (d / "notes.txt").write_text("not media", encoding="utf-8")
    return d


def _draft(store, name, bin_paths=(), tracks=None):
    """A CapCut-made draft: empty timeline, footage imported into its Media panel."""
    folder = store.root / name
    folder.mkdir()
    d = schema.empty_draft(1920, 1080, 30.0, {"app_version": "9.5.0"})
    if tracks:
        d["tracks"] = tracks
    (folder / "draft_info.json").write_text(json.dumps(d), encoding="utf-8")
    kinds = {"video": "video", "image": "photo", "audio": "music"}
    meta = {"draft_name": name, "tm_duration": 0, "draft_materials": [
        {"type": 0, "value": [{"file_Path": str(p), "metetype": kinds[media.kind(p)],
                               "duration": 5_000_000, "import_time_ms": i}
                              for i, p in enumerate(bin_paths)]},
        {"type": 1, "value": []}]}
    (folder / "draft_meta_info.json").write_text(json.dumps(meta), encoding="utf-8")
    reg = store.registry()
    reg["all_draft_store"].insert(0, {"draft_name": name, "draft_fold_path": str(folder),
                                      "tm_duration": 0})
    (store.root / "root_meta_info.json").write_text(json.dumps(reg), encoding="utf-8")
    return folder


# ------------------------------------------------------------------ finding projects


def test_resolve_loose_names(store):
    for n in ("TestY", "TestY-edit", "My Vlog 2"):
        _draft(store, n)
    assert store.resolve("TestY") == "TestY"
    assert store.resolve("testy") == "TestY"               # case
    assert store.resolve("my vlog 2") == "My Vlog 2"
    assert store.resolve("myvlog") == "My Vlog 2"          # prefix
    assert store.resolve("testy edit") == "TestY-edit"
    assert store.resolve(str(store.root / "TestY")) == "TestY"   # draft folder path
    with pytest.raises(DraftError, match="no CapCut draft"):
        store.resolve("holiday")


def test_resolve_ambiguous(store):
    _draft(store, "trip-day1")
    _draft(store, "trip-day2")
    with pytest.raises(DraftError, match="several"):
        store.resolve("trip")


def test_bin_media(store, footage, tmp_path):
    gone = tmp_path / "deleted.mp4"
    _draft(store, "P", [footage / "a-wide.mp4", gone, footage / "song.wav"])
    items = store.bin_media("P")
    assert [m["kind"] for m in items] == ["video", "video", "audio"]
    assert [m["exists"] for m in items] == [True, False, True]


def test_gather_from_project_and_folder(store, footage, media_dir):
    _draft(store, "Trip", [footage / "a-wide.mp4"])
    src = auto.gather("trip", store)
    assert src.name == "Trip" and src.exists
    assert src.footage.videos == [str(footage / "a-wide.mp4")]
    # adding a folder merges, without duplicates, and skips non-media
    src = auto.gather("trip", store, folder=str(footage))
    assert len(src.footage.videos) == 2
    assert src.footage.images == [str(footage / "c-still.png")]
    assert src.footage.music == [str(footage / "song.wav")]


def test_gather_folder_alone_names_new_project(store, footage):
    src = auto.gather(str(footage), store)
    assert src.name == "footage" and not src.exists


def test_gather_empty_project_explains(store):
    _draft(store, "Empty")
    with pytest.raises(DraftError, match="Media panel"):
        auto.gather("empty", store)


# ------------------------------------------------------------------ auto edits


def test_auto_montage_into_existing_project(store, footage):
    folder = _draft(store, "Trip", [footage / "a-wide.mp4", footage / "b-tall.mp4",
                                    footage / "c-still.png", footage / "song.wav"])
    (folder / "Timelines").mkdir()           # CapCut's cache; must be moved aside
    before = json.loads((folder / "draft_info.json").read_text(encoding="utf-8"))
    out = auto.auto_edit("trip", store=store, max_seconds=12, log=lambda *_: None)
    assert out["style"] == "montage" and out["draft"] == "Trip" and not out["new_draft"]
    assert out["canvas"] == "1920x1080"            # most screen time is landscape
    d = store.load("Trip")
    assert d["id"] == before["id"]                  # same project, not a copy
    assert d["canvas_config"]["ratio"] == "16:9"
    assert schema.check(d) == []
    main = cc.segments(d, "main")
    assert len(main) >= 4
    assert all(s["volume"] == 0.0 for _, s in main)          # music carries a montage
    assert all(s["common_keyframes"] for _, s in main)       # camera moves
    assert len(cc.segments(d, "audio")) == 1
    assert not (folder / "Timelines").exists()
    meta = store.meta("Trip")
    assert meta["tm_duration"] == d["duration"] > 0           # CapCut's home screen
    assert len(meta["draft_materials"][0]["value"]) == 4      # Media panel untouched
    reg = store.registry()["all_draft_store"][0]
    assert reg["tm_duration"] == d["duration"]
    assert (folder / "draft_cover.jpg").exists()
    # every cut lands on a kick of the music (one every 0.5s from 0), within a frame
    src_in = cc.segments(d, "audio")[0][1]["source_timerange"]["start"] / 1e6
    song = [s["target_timerange"]["start"] / 1e6 + src_in for _, s in main]
    assert all(abs(t / 0.5 - round(t / 0.5)) * 0.5 <= 1 / 25 + 0.03 for t in song), song


def test_auto_refuses_to_overwrite_hand_edits(store, footage):
    seg = schema.segment("m", [], 0, 1_000_000, 0)
    trk = schema.track("video")
    trk["segments"].append(seg)
    _draft(store, "Done", [footage / "a-wide.mp4", footage / "song.wav"], tracks=[trk])
    with pytest.raises(DraftError, match="replace"):
        auto.auto_edit("done", store=store, log=lambda *_: None)
    out = auto.auto_edit("done", store=store, replace=True, max_seconds=6,
                         log=lambda *_: None)
    assert out["backup"]
    store.restore("Done")
    assert len(store.load("Done")["tracks"][0]["segments"]) == 1   # hand edit back


def test_auto_folder_makes_new_project_without_music(store, footage, tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    for f in ("a-wide.mp4", "b-tall.mp4"):
        shutil.copy(footage / f, clips / f)
    out = auto.auto_edit(str(clips), store=store, aspect="9:16", log=lambda *_: None)
    assert out["new_draft"] and out["draft"] == "clips"
    d = store.load("clips")
    assert d["canvas_config"] == {**d["canvas_config"], "width": 1080, "height": 1920,
                                  "ratio": "9:16"}
    audio = cc.segments(d, "audio")
    assert len(audio) == 1                        # generated ambient bed
    assert any(e["draft_name"] == "clips" for e in store.list())


def test_auto_dry_run_writes_nothing(store, footage):
    folder = _draft(store, "Trip", [footage / "a-wide.mp4", footage / "song.wav"])
    before = (folder / "draft_info.json").read_text(encoding="utf-8")
    out = auto.auto_edit("trip", store=store, dry_run=True, log=lambda *_: None)
    assert out["dry_run"] and (folder / "draft_info.json").read_text(encoding="utf-8") == before
    assert not store.list_backups("Trip")


def test_auto_talking_head(store, media_dir, footage, monkeypatch):
    """Speech found -> cut on words, captions, b-roll cutaways, music ducked."""
    words = [{"word": w, "start": s, "end": e} for w, s, e in [
        ("So", 0.0, 0.3), ("um", 0.4, 0.6), ("this", 0.65, 0.9), ("is", 0.95, 1.1),
        ("capcut.", 1.15, 1.4), ("It", 2.6, 2.8), ("edits", 2.85, 3.1),
        ("videos", 3.15, 3.5), ("for", 3.55, 3.7), ("you.", 3.75, 3.95)]]
    monkeypatch.setattr(transcribe, "available", lambda: True)
    monkeypatch.setattr(transcribe, "cached", lambda path, *a, **k: {
        "words": words if path.endswith("talk.mp4") else []})
    _draft(store, "Vlog", [media_dir / "talk.mp4", footage / "a-wide.mp4",
                           footage / "song.wav"])
    out = auto.auto_edit("vlog", store=store, hook="watch this", log=lambda *_: None)
    assert out["style"] == "talking"
    d = store.load("Vlog")
    texts = [schema.text_content(m) for m in d["materials"]["texts"]]
    assert "watch this" in texts and any("CapCut" in t for t in texts)
    assert not any("um" in t.split() for t in texts)
    assert sum(s["target_timerange"]["duration"] for _, s in cc.segments(d, "main")) < 3.9e6
    music = [s for _, s in cc.segments(d, "audio")]
    assert music and all(s["volume"] < 0.2 for s in music)


def test_auto_talking_requires_speech(store, footage, monkeypatch):
    monkeypatch.setattr(transcribe, "available", lambda: True)
    monkeypatch.setattr(transcribe, "cached", lambda *a, **k: {"words": []})
    _draft(store, "Quiet", [footage / "a-wide.mp4"])
    with pytest.raises(DraftError, match="talking"):
        auto.auto_edit("quiet", store=store, style="talking", log=lambda *_: None)


# ------------------------------------------------------------------ fixes


def test_probe_reads_phone_rotation(media_dir):
    out = media_dir / "rotated.mov"
    ff("-display_rotation:v:0", "90", "-i", str(media_dir / "talk.mp4"), "-c", "copy",
       str(out))
    info = media.probe(out)
    assert (info.width, info.height) == (360, 640)


def test_probe_ignores_cover_art(media_dir, tmp_path):
    out = tmp_path / "song.m4a"
    ff("-i", str(media_dir / "music.wav"), "-i", str(media_dir / "badge.png"),
       "-map", "0", "-map", "1", "-c:a", "aac", "-c:v", "png",
       "-disposition:v", "attached_pic", str(out))
    assert not media.probe(out).has_video


def test_montage_accepts_stills(footage):
    tl = beats.montage([beats.Shot(str(footage / "c-still.png")),
                        beats.Shot(str(footage / "a-wide.mp4"))],
                       footage / "song.wav", bars=2, log=lambda *_: None)
    assert any(c.source.endswith(".png") for c in tl.clips)


def test_retakes_up_to_eight_words():
    text = "So this is the first take this is the first take of my video"
    ws = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
          for i, w in enumerate(text.split())]
    kept = " ".join(w["word"] for w in cuts._drop_retakes(ws))
    assert kept == "So this is the first take of my video"


def test_backups_sort_in_time_order(store, footage):
    _draft(store, "B")
    for i in range(5):
        store.backup("B", f"step {i}")
    reasons = [(b / "reason.txt").read_text(encoding="utf-8") for b in store.list_backups("B")]
    assert reasons == [f"step {i}" for i in range(5)]


def test_refuses_multi_timeline_drafts(store):
    folder = _draft(store, "Multi")
    (folder / "Timelines").mkdir()
    (folder / "Timelines/project.json").write_text(json.dumps(
        {"timelines": [{"id": "A"}, {"id": "B"}]}), encoding="utf-8")
    with pytest.raises(DraftError, match="timelines"):
        store.save("Multi", store.load("Multi"), "x")


def test_clip_volume_in_render(media_dir, tmp_path):
    from capcut_forge.render.ffmpeg import build_command
    tl = Timeline(clips=[Clip(str(media_dir / "talk.mp4"), 0, 1, volume=0.0)],
                  width=320, height=180)
    graph = tmp_path / "w"
    graph.mkdir()
    build_command(tl, tmp_path / "o.mp4", graph)
    assert "anullsrc" in (graph / "graph.txt").read_text(encoding="utf-8")


def test_letterbox_taller_than_canvas(tmp_path):
    png = cc.letterbox_png(tmp_path / "l.png", 1920, 1080, 1.0)
    assert png.exists()


def test_capcut_placeholder_paths(store, footage):
    """CapCut 9.5 re-saves in-draft paths with a placeholder for the folder."""
    folder = _draft(store, "Saved")
    res = folder / "Resources"
    res.mkdir()
    shutil.copy(footage / "a-wide.mp4", res / "a-wide.mp4")
    d = store.load("Saved")
    d["materials"]["videos"].append(
        {"id": "X", "path": "##_draftpath_placeholder_0E68-AB_##/Resources/a-wide.mp4"})
    (folder / "draft_info.json").write_text(json.dumps(d), encoding="utf-8")
    meta = store.meta("Saved")
    meta["draft_materials"][0]["value"].append(
        {"file_Path": "./Resources/a-wide.mp4", "metetype": "video", "duration": 1})
    (folder / "draft_meta_info.json").write_text(json.dumps(meta), encoding="utf-8")
    assert store.bin_media("Saved")[0]["exists"]
    assert auto.gather("saved", store).footage.videos == [str(res / "a-wide.mp4")]


def test_punch_captions_keep_short_words():
    from capcut_forge import captions
    ws = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.12}
          for i, w in enumerate("this is the first take".split())]
    assert [c.text for c in captions.build(ws, "punch")] == ["this", "is", "the", "first", "take"]


def test_blocked_footage_explains_the_fix(store, footage, tmp_path, monkeypatch):
    """macOS privacy blocks show up as 'exists, but can't be read'."""
    clip = tmp_path / "Downloads" / "a.mp4"
    clip.parent.mkdir()
    shutil.copy(footage / "a-wide.mp4", clip)
    _draft(store, "Blocked", [clip])
    real_open = open

    def tcc_open(p, *a, **k):
        if str(p) == str(clip):
            raise PermissionError(1, "Operation not permitted")
        return real_open(p, *a, **k)
    monkeypatch.setattr("builtins.open", tcc_open)
    monkeypatch.setattr(media.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(media.sys, "platform", "darwin")
    with pytest.raises(media.AccessError) as e:
        auto.auto_edit("blocked", store=store, log=lambda *_: None)
    msg = str(e.value)
    assert "Privacy & Security" in msg and '"Downloads Folder"' in msg and str(clip) in msg


def test_blocked_folder_explains_the_fix(store, tmp_path, monkeypatch):
    folder = tmp_path / "clips"
    folder.mkdir()

    def denied(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(auto, "scan", denied)
    with pytest.raises(media.AccessError, match="clips"):
        auto.gather(str(folder), store)
