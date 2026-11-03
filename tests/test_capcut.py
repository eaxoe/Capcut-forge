import json

import pytest

from capcut_forge.capcut import app, build as cc, schema
from capcut_forge.capcut.store import DraftError
from capcut_forge.timeline import AudioTrack, Caption, Clip, Overlay, Timeline


@pytest.fixture
def timeline(media_dir):
    v = str(media_dir / "talk.mp4")
    return Timeline(
        clips=[Clip(v, 0.0, 1.4), Clip(v, 2.5, 4.0, zoom=1.15)],
        overlays=[Overlay(str(media_dir / "badge.png"), at=0.5, duration=1.0)],
        captions=[Caption("hello there", 0.0, 1.0), Caption("big hook", 0.0, 1.2,
                                                            style="hook", role="hook")],
        audio=[AudioTrack(str(media_dir / "music.wav"), volume=0.2, fade_out=0.5, loop=True)],
    )


def test_build_creates_valid_registered_draft(store, timeline):
    out = cc.build_draft(timeline, "t1", store)
    d = store.load("t1")
    assert schema.check(d) == []
    assert d["duration"] == schema.us(1.4) + schema.us(1.5)
    assert [e["draft_name"] for e in store.list()] == ["t1"]
    assert set(out["tracks"]) == {"main", "overlay", "text", "audio"}
    # hook and caption overlap in time -> two text tracks
    assert len([t for t in d["tracks"] if t["type"] == "text"]) == 2
    # music loops a 2s file across the 2.9s timeline
    assert len(out["tracks"]["audio"]) == 2
    # media staged inside the draft, fonts too
    res = store.folder("t1") / "Resources"
    assert (res / "talk.mp4").exists() and (res / "Inter-Bold.otf").exists()
    main = cc.segments(d, "main")
    # fills the 1080x1920 canvas like the renderer: 640x360 -> cover/fit = 3.16x
    assert abs(main[1][1]["clip"]["scale"]["x"] - 1.15 * (1920 / 360) / (1080 / 640)) < 1e-3
    assert main[1][1]["source_timerange"]["start"] == schema.us(2.5)


def test_text_material_style_run_covers_text(store, timeline):
    cc.build_draft(timeline, "t2", store)
    d = store.load("t2")
    for m in d["materials"]["texts"]:
        content = json.loads(m["content"])
        assert content["styles"][0]["range"] == [0, len(content["text"])]


def test_build_refuses_existing_and_cleans_up_failures(store, timeline, media_dir):
    cc.build_draft(timeline, "dup", store)
    with pytest.raises(DraftError):
        cc.build_draft(timeline, "dup", store)
    bad = Timeline(clips=[Clip(str(media_dir / "nope.mp4"), 0, 1)])
    with pytest.raises(DraftError):
        cc.build_draft(bad, "bad", store)
    assert not store.folder("bad").exists()


def test_refuses_to_write_while_capcut_open(store, timeline, monkeypatch):
    monkeypatch.setattr(app, "is_running", lambda: True)
    monkeypatch.setattr(type(store), "is_live", property(lambda self: True))
    with pytest.raises(app.AppRunningError):
        cc.build_draft(timeline, "open", store)
    assert not store.folder("open").exists()


def test_edit_backup_and_restore(store, timeline):
    cc.build_draft(timeline, "e", store)
    (store.folder("e") / "Timelines").mkdir()
    before = store.load("e")
    cap = Caption("added later", 2.0, 2.5)
    out = cc.edit("e", lambda d, ctx: cc.add_caption(ctx, cap), "test", store)
    assert out["backup"]
    assert not (store.folder("e") / "Timelines").exists()   # forces CapCut re-import
    after = store.load("e")
    assert len(after["materials"]["texts"]) == len(before["materials"]["texts"]) + 1
    store.restore("e")
    assert store.load("e") == before
    assert (store.folder("e") / "Timelines").exists()


def test_dry_run_writes_nothing(store, timeline):
    cc.build_draft(timeline, "dry", store)
    raw = store.draft_file("dry").read_text(encoding="utf-8")
    res = cc.edit("dry", lambda d, ctx: cc.remove(d, "overlay", 0), "x", store, dry_run=True)
    assert res["dry_run"] and "overlay" not in res["after"]["tracks"]
    assert store.draft_file("dry").read_text(encoding="utf-8") == raw
    assert store.list_backups("dry") == []


def test_remove_drops_orphaned_materials(store, timeline):
    cc.build_draft(timeline, "r", store)
    cc.edit("r", lambda d, ctx: cc.remove(d, "overlay", 0), "rm", store)
    d = store.load("r")
    assert schema.check(d) == []
    assert not any(m["type"] == "photo" for m in d["materials"]["videos"])


def test_keyframes_use_source_clock(store, timeline):
    cc.build_draft(timeline, "k", store)

    def mutate(d, ctx):
        seg = cc.pick(d, "main", 1)[1]                 # source in-point 2.5s, timeline 1.4s
        schema.add_keyframe(seg, "scale", schema.us(1.5), 1.0)
        schema.add_keyframe(seg, "scale", schema.us(2.5), 1.3)
        with pytest.raises(ValueError):
            schema.add_keyframe(seg, "scale", schema.us(0.5), 1.0)
    cc.edit("k", mutate, "kf", store)
    seg = cc.pick(store.load("k"), "main", 1)[1]
    xs = next(e for e in seg["common_keyframes"] if e["property_type"] == "KFTypeScaleX")
    assert [p["time_offset"] for p in xs["keyframe_list"]] == [schema.us(2.6), schema.us(3.6)]


def test_times_snap_to_frame_grid(store, media_dir):
    """CapCut snaps to frames on open; we write snapped times so nothing moves."""
    v = str(media_dir / "talk.mp4")
    tl = Timeline(clips=[Clip(v, 0.02, 1.03), Clip(v, 1.3, 1.98), Clip(v, 2.52, 3.9)],
                  captions=[Caption("x", 0.08, 1.08)])
    cc.build_draft(tl, "snap", store)
    d = store.load("snap")
    frame = schema.US / 30
    end = 0
    for _, s in cc.segments(d, "main"):
        for v_ in (s["source_timerange"]["start"], s["target_timerange"]["start"],
                   s["target_timerange"]["duration"]):
            assert abs(v_ / frame - round(v_ / frame)) < 1e-3
        assert s["target_timerange"]["start"] == end          # no gaps or overlaps
        end += s["target_timerange"]["duration"]
    cap = cc.segments(d, "text")[0][1]["target_timerange"]
    assert (cap["start"], cap["duration"]) == (100000, 1000000)   # frames 3..33, as CapCut does


def test_repair_fills_null_source_range():
    d = {"tracks": [{"segments": [{"source_timerange": None,
                                   "target_timerange": {"start": 5, "duration": 10}}]}]}
    schema.repair(d)
    assert d["tracks"][0]["segments"][0]["source_timerange"] == {"start": 0, "duration": 10}
    assert d["duration"] == 15


def test_scratch_root_ignores_open_app(store, timeline, monkeypatch):
    monkeypatch.setattr(app, "is_running", lambda: True)
    cc.build_draft(timeline, "scratch", store)          # not CapCut's folder: allowed
    assert store.exists("scratch")


def test_rejects_path_traversal_names(store):
    for bad in ("../x", ".hidden", ""):
        with pytest.raises(DraftError):
            store.folder(bad)


def test_grade_writes_adjust_sliders_and_regrades(store, timeline):
    from capcut_forge import grade
    timeline.grade = grade.resolve("soft-cinematic")
    cc.build_draft(timeline, "g", store)
    d = store.load("g")

    def adjust(seg, draft):
        by = {e["id"]: e for e in draft["materials"]["effects"]}
        return {by[r]["type"]: by[r]["value"] for r in seg["extra_material_refs"] if r in by}

    for _, seg in cc.segments(d, "main"):
        assert adjust(seg, d) == timeline.grade
    assert schema.check(d) == []
    # re-grade: old sliders replaced, not stacked; orphans removed
    g2 = grade.resolve("moody-teal")
    cc.edit("g", lambda dd, ctx: cc.apply_grade(dd, ctx, g2), "regrade", store)
    d = store.load("g")
    for _, seg in cc.segments(d, "main"):
        assert adjust(seg, d) == g2
    assert len(d["materials"]["effects"]) == len(g2) * len(cc.segments(d, "main"))


def test_restyle_per_shot_grades_motion_and_bars(store, timeline):
    from capcut_forge import grade, motion
    cc.build_draft(timeline, "rs", store)
    timeline.grade = grade.resolve("cinematic")
    timeline.clips[0].grade = {"brightness": 0.1}
    timeline.clips[1].grade = {"temperature": -0.2}
    timeline.clips[0].keyframes = motion.fade(motion.preset("pull-out", 1.4), 1.4, fade_in=0.5)
    timeline.clips[1].keyframes = motion.preset("drift", 1.5)
    timeline.letterbox = 2.39
    for _ in range(2):        # idempotent: running twice must not stack anything
        cc.edit("rs", lambda d, ctx: cc.restyle(d, ctx, timeline), "restyle", store)
    d = store.load("rs")
    assert schema.check(d) == []
    eff = {e["id"]: e for e in d["materials"]["effects"]}
    looks = [{eff[r]["type"]: eff[r]["value"] for r in s["extra_material_refs"] if r in eff}
             for _, s in cc.segments(d, "main")]
    assert looks[0]["brightness"] == 0.1 and looks[1]["temperature"] == round(0.05 - 0.2, 4)
    kinds = [{e["property_type"] for e in s["common_keyframes"]} for _, s in cc.segments(d, "main")]
    assert {"KFTypeAlpha", "KFTypeScaleX"} <= kinds[0]
    assert {"KFTypeRotation", "KFTypePositionX", "KFTypePositionY"} <= kinds[1]
    bars = [s for _, s in cc.segments(d, "overlay")
            if any(m["id"] == s["material_id"] and m["material_name"].startswith("letterbox")
                   for m in d["materials"]["videos"])]
    assert len(bars) == 1
    assert len(eff) == sum(len(x) for x in looks)          # old sliders not left behind


def test_restyle_refuses_mismatched_plan(store, timeline):
    cc.build_draft(timeline, "mm", store)
    timeline.clips = timeline.clips[:1]
    with pytest.raises(DraftError):
        cc.edit("mm", lambda d, ctx: cc.restyle(d, ctx, timeline), "x", store)
