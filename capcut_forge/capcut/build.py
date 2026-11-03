"""Timeline -> CapCut draft, plus additive edits to existing drafts."""
from __future__ import annotations

import copy
import math
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from .. import media, styles
from ..timeline import AudioTrack, Caption, Timeline
from . import app, schema
from .schema import US, us
from .store import PRIMARY, DraftError, DraftStore, atomic_write_json

ROLES = ("main", "overlay", "text", "audio")


def role_of(track: dict) -> str:
    if track["type"] == "video":
        return "overlay" if track.get("flag") == 2 else "main"
    return track["type"]


def segments(draft: dict, role: str) -> list[tuple[dict, dict]]:
    """(track, segment) pairs for a role, flattened across tracks in timeline
    order. CapCut splits overlapping segments onto separate tracks, so a
    per-track index is not a stable address; this is."""
    if role not in ROLES:
        raise DraftError(f"unknown track role {role!r} (use {', '.join(ROLES)})")
    pairs = [(t, s) for t in draft["tracks"] if role_of(t) == role for s in t["segments"]]
    return sorted(pairs, key=lambda p: p[1]["target_timerange"]["start"])


def pick(draft: dict, role: str, index: int) -> tuple[dict, dict]:
    pairs = segments(draft, role)
    if not 0 <= index < len(pairs):
        raise DraftError(f"{role} has {len(pairs)} segment(s), no index {index}")
    return pairs[index]


def summary(draft: dict) -> dict:
    """Readable snapshot of a draft: tracks, segment times, what's on them."""
    mats = {m["id"]: (cat, m) for cat, rows in draft["materials"].items()
            for m in rows if isinstance(m, dict) and "id" in m}
    out = {"duration": round(draft.get("duration", 0) / US, 3),
           "canvas": [draft["canvas_config"]["width"], draft["canvas_config"]["height"]],
           "tracks": {}}
    for role in ROLES:
        rows = []
        for i, (_, s) in enumerate(segments(draft, role)):
            cat, m = mats.get(s["material_id"], ("?", {}))
            label = (schema.text_content(m)[:40] if cat == "texts"
                     else m.get("material_name") or m.get("name") or "?")
            tr = s["target_timerange"]
            rows.append({"i": i, "media": label, "start": round(tr["start"] / US, 3),
                         "dur": round(tr["duration"] / US, 3)})
        if rows:
            out["tracks"][role] = rows
    return out


# ------------------------------------------------------------------ builders


@dataclass
class _Ctx:
    store: DraftStore
    name: str
    draft: dict
    fps: float
    _mats: dict

    def frames(self, seconds: float) -> int:
        # CapCut rounds UP to the next frame boundary (measured on 9.3).
        return math.ceil(seconds * self.fps - 1e-6)

    def at(self, frame: int) -> int:
        """Frame number -> microseconds. CapCut snaps every time to the frame
        grid when it opens a draft, so we write snapped times up front and the
        plan, the draft and the render agree exactly."""
        return math.floor(frame * US / self.fps + 1e-6)   # CapCut floors µs too

    def t(self, seconds: float) -> int:
        return self.at(self.frames(seconds))

    def span(self, start: float, end: float) -> tuple[int, int]:
        """(start_us, duration_us) with both ends on frame boundaries."""
        a, b = self.frames(start), self.frames(end)
        return self.at(a), self.at(b) - self.at(a)

    def video_mat(self, src: str) -> tuple[str, media.MediaInfo]:
        """One material per distinct source file, staged into the draft."""
        if src not in self._mats:
            staged = self.store.stage_media(self.name, src)
            info = media.probe(staged)
            m = schema.video_material(staged, info.width or 0, info.height or 0,
                                      us(info.duration), info.has_audio, photo=info.is_image)
            self.draft["materials"]["videos"].append(m)
            self._mats[src] = (m["id"], info)
        return self._mats[src]

    def bundle(self) -> str:
        if "_bundle" not in self._mats:
            self._mats["_bundle"] = self.store.adjust_bundle()
        return self._mats["_bundle"]

    def font_path(self, font: str) -> str | None:
        """Copy the style's font into the draft (sandbox-readable) and point at it."""
        src = Path(font)
        if not src.exists():
            return None
        return str(self.store.stage_media(self.name, src))


def _ctx(store: DraftStore, name: str, draft: dict) -> _Ctx:
    return _Ctx(store, name, draft, float(draft.get("fps") or 30.0), {})


def _place_main(ctx: _Ctx, tl: Timeline) -> None:
    trk = schema.track("video")
    pos = 0                                  # timeline position in frames
    for c in tl.clips:
        mid, info = ctx.video_mat(c.source)
        n = max(1, ctx.frames(c.end) - ctx.frames(c.start))
        start, dur = ctx.at(pos), ctx.at(pos + n) - ctx.at(pos)
        src = 0 if info.is_image else ctx.t(c.start)
        refs = schema._extras(ctx.draft["materials"], "video")
        look = tl.clip_grade(c)
        if look:
            refs += schema.adjust_materials(ctx.draft["materials"], look, ctx.bundle())
        base = c.zoom * fill_scale(info, tl.width, tl.height)
        seg = schema.segment(mid, refs, src, dur, start, volume=c.volume, scale=base,
                             x=c.x, y=c.y)
        set_motion(ctx, seg, c.keyframes, base)
        trk["segments"].append(seg)
        pos += n
    ctx.draft["tracks"].append(trk)


def fill_scale(info, canvas_w: int, canvas_h: int) -> float:
    """CapCut fits media inside the canvas by default (bars on mismatched
    aspect); the ffmpeg renderer fills. This scale makes CapCut fill too, so
    both backends frame the shot identically."""
    if not info.width or not info.height:
        return 1.0
    fit = min(canvas_w / info.width, canvas_h / info.height)
    cover = max(canvas_w / info.width, canvas_h / info.height)
    return round(cover / fit, 5)


_KF_PROPS = {"scale": "scale", "x": "x", "y": "y", "rotation": "rotation", "opacity": "opacity"}


def set_motion(ctx: "_Ctx", seg: dict, keys: dict, base_scale: float) -> None:
    """Replace a segment's keyframes with `keys` (clip-relative seconds).
    Scale values multiply the segment's base (zoom x fill) scale."""
    seg["common_keyframes"] = []
    if not keys:
        return
    start = seg["target_timerange"]["start"]
    last = start + seg["target_timerange"]["duration"] - ctx.at(1)
    for prop, pts in keys.items():
        if prop not in _KF_PROPS:
            raise DraftError(f"unknown keyframe property {prop!r}")
        for t, v in pts:
            at = min(start + ctx.t(t), last)
            schema.add_keyframe(seg, _KF_PROPS[prop], at,
                                v * base_scale if prop == "scale" else v)
    # The static values show wherever no keyframe applies; keep them consistent.
    first = {p: pts[0][1] for p, pts in keys.items()}
    transform(seg, scale=first["scale"] * base_scale if "scale" in first else None,
              x=first.get("x"), y=first.get("y"), rotation=first.get("rotation"),
              opacity=first.get("opacity"))


def letterbox_png(path: Path, w: int, h: int, aspect: float) -> Path:
    from PIL import Image, ImageDraw
    bar = max(0, round((h - w / aspect) / 2))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if bar:                       # an aspect taller than the canvas needs no bars
        d.rectangle((0, 0, w, bar - 1), fill=(0, 0, 0, 255))
        d.rectangle((0, h - bar, w, h), fill=(0, 0, 0, 255))
    img.save(path)
    return path


def add_letterbox(ctx: "_Ctx", aspect: float, length_us: int, layer: int = 9) -> dict:
    """Cinema bars as a full-canvas PNG on a high overlay layer, spanning the
    edit. Delete that one overlay in CapCut to remove them."""
    cw, ch = ctx.draft["canvas_config"]["width"], ctx.draft["canvas_config"]["height"]
    res = ctx.store.folder(ctx.name) / "Resources"
    res.mkdir(parents=True, exist_ok=True)
    png = letterbox_png(res / f"letterbox-{aspect:.2f}.png", cw, ch, aspect)
    mid, _ = ctx.video_mat(str(png))
    seg = schema.segment(mid, schema._extras(ctx.draft["materials"], "video"),
                         0, length_us, 0, render_index=layer, track_render_index=layer)
    overlay_track(ctx.draft, layer)["segments"].append(seg)
    return seg


def overlay_track(draft: dict, layer: int) -> dict:
    existing = [t for t in draft["tracks"] if role_of(t) == "overlay"]
    while len(existing) < layer:
        t = schema.track("video", flag=2)
        draft["tracks"].append(t)
        existing.append(t)
    return existing[layer - 1]


def add_overlay(ctx: _Ctx, ov) -> dict:
    mid, info = ctx.video_mat(ov.source)
    length = ov.duration if ov.duration is not None else (info.duration - ov.src_in) or 3.0
    start, dur = ctx.span(ov.at, ov.at + length)
    seg = schema.segment(mid, schema._extras(ctx.draft["materials"], "video"),
                         ctx.t(ov.src_in), dur, start,
                         volume=0.0 if ov.mute else 1.0, render_index=ov.layer,
                         track_render_index=ov.layer, scale=ov.scale, x=ov.x, y=ov.y)
    overlay_track(ctx.draft, ov.layer)["segments"].append(seg)
    return seg


def _free_track(draft: dict, kind: str, start: int, end: int, flag: int = 0) -> dict:
    """First track of this kind with no segment overlapping [start, end)."""
    for t in draft["tracks"]:
        if t["type"] != kind or t.get("flag", 0) != flag:
            continue
        if all(s["target_timerange"]["start"] >= end or
               s["target_timerange"]["start"] + s["target_timerange"]["duration"] <= start
               for s in t["segments"]):
            return t
    t = schema.track(kind, flag)
    draft["tracks"].append(t)
    return t


def add_caption(ctx: _Ctx, cap: Caption, style=None) -> dict:
    style = style or styles.get(cap.style)
    style = style.with_(capcut_font_path=ctx.font_path(style.font))
    text = cap.text.upper() if style.uppercase else cap.text
    mat = schema.text_material(text, style)
    ctx.draft["materials"]["texts"].append(mat)
    start, dur = ctx.span(cap.start, cap.end)
    seg = schema.segment(mat["id"], schema._extras(ctx.draft["materials"], "text"),
                         0, dur, start, kind="text",
                         render_index=schema.TEXT_RENDER_INDEX + (1 if cap.role == "hook" else 0),
                         track_render_index=1, y=style.capcut_y)
    _free_track(ctx.draft, "text", start, start + dur)["segments"].append(seg)
    return seg


def add_audio(ctx: _Ctx, a: AudioTrack, timeline_end_us: int) -> list[dict]:
    staged = ctx.store.stage_media(ctx.name, a.source)
    info = media.probe(staged)
    mat = schema.audio_material(staged, us(info.duration))
    ctx.draft["materials"]["audios"].append(mat)
    media_end = ctx.at(int(info.duration * ctx.fps))   # last whole frame of the file
    want_end = (ctx.t(a.at + a.duration) if a.duration is not None
                else (timeline_end_us if a.loop else ctx.t(a.at) + media_end - ctx.t(a.src_in)))
    placed, t, src_in = [], ctx.t(a.at), ctx.t(a.src_in)
    while t < want_end:
        length = min(want_end - t, media_end - src_in)
        if length <= 0:
            break
        first, last = not placed, t + length >= want_end
        fade = (us(a.fade_in) if first else 0, us(a.fade_out) if last else 0)
        seg = schema.segment(mat["id"], schema._extras(ctx.draft["materials"], "audio",
                                                       fade if any(fade) else None),
                             src_in, length, t, kind="audio", volume=a.volume)
        _free_track(ctx.draft, "audio", t, t + length)["segments"].append(seg)
        placed.append(seg)
        t += length
        src_in = 0
        if not a.loop:
            break
    return placed


def _meta(name: str, folder: Path, draft: dict, now_us: int) -> dict:
    return {
        "cloud_draft_cover": False, "cloud_draft_sync": False,
        "draft_cover": "draft_cover.jpg", "draft_fold_path": str(folder),
        "draft_id": draft["id"], "draft_name": name, "draft_root_path": str(folder.parent),
        "draft_is_invisible": False, "draft_materials": [
            {"type": t, "value": []} for t in (0, 1, 2, 3, 6, 7)],
        "draft_materials_copied_info": [], "draft_need_rename_folder": False,
        "draft_new_version": "", "draft_removable_storage_device": "",
        "draft_segment_extra_info": [], "draft_timeline_materials_size_": 0,
        "draft_type": "", "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0, "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1,
        "tm_draft_create": now_us, "tm_draft_modified": now_us, "tm_draft_removed": 0,
        "tm_duration": draft["duration"],
    }


def _registry_entry(name: str, folder: Path, draft: dict, now_us: int) -> dict:
    return {
        "cloud_draft_cover": False, "cloud_draft_sync": False,
        "draft_cover": str(folder / "draft_cover.jpg"), "draft_fold_path": str(folder),
        "draft_id": draft["id"], "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False, "draft_is_invisible": False,
        "draft_json_file": str(folder / PRIMARY), "draft_name": name,
        "draft_new_version": "", "draft_root_path": str(folder.parent),
        "draft_timeline_materials_size": 0, "draft_type": "",
        "streaming_edit_draft_ready": True, "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1, "tm_draft_create": now_us,
        "tm_draft_modified": now_us, "tm_draft_removed": 0,
        "tm_duration": draft["duration"],
    }


RATIOS = {(16, 9): "16:9", (9, 16): "9:16", (1, 1): "1:1", (4, 3): "4:3", (3, 4): "3:4"}


def _canvas(draft: dict, w: int, h: int) -> None:
    """A named ratio pins the canvas. "original" lets CapCut re-derive it from
    the first clip, which flips a 9:16 edit that opens on a landscape shot."""
    g = math.gcd(w, h)
    draft["canvas_config"].update(width=w, height=h,
                                  ratio=RATIOS.get((w // g, h // g), "original"))


def _populate(ctx: _Ctx, tl: Timeline) -> None:
    """Lay a whole timeline into ctx.draft (which has no tracks yet)."""
    _canvas(ctx.draft, tl.width, tl.height)
    _place_main(ctx, tl)
    main_end = sum(s["target_timerange"]["duration"] for s in ctx.draft["tracks"][0]["segments"])
    for ov in tl.overlays:
        add_overlay(ctx, ov)
    if tl.letterbox:
        add_letterbox(ctx, tl.letterbox, main_end)
    for cap in tl.captions:
        add_caption(ctx, cap)
    for a in tl.audio:
        add_audio(ctx, a, main_end)
    schema.repair(ctx.draft)
    problems = schema.check(ctx.draft)
    if problems:
        raise DraftError("generated draft failed checks:\n  " + "\n  ".join(problems))


def _plan_summary(tl: Timeline, name: str, **extra) -> dict:
    return {"dry_run": True, "name": name, "clips": len(tl.clips),
            "overlays": len(tl.overlays), "captions": len(tl.captions),
            "audio": len(tl.audio), "duration": round(tl.duration, 3),
            "canvas": [tl.width, tl.height], **extra}


def _cover(tl: Timeline, out: Path) -> None:
    first = tl.clips[0]
    media.extract_frame(first.source, 0 if media.kind(first.source) == "image" else first.start,
                        out)


def build_draft(tl: Timeline, name: str, store: DraftStore | None = None,
                allow_quit: bool = False, dry_run: bool = False,
                relaunch: bool = True) -> dict:
    """Create a NEW CapCut draft from a timeline. Returns the draft summary."""
    store = store or DraftStore()
    problems = tl.validate()
    if problems:
        raise DraftError("timeline is not buildable:\n  " + "\n  ".join(problems))
    if store.folder(name).exists():
        raise DraftError(f"draft {name!r} already exists — pick another --name")
    if dry_run:
        return _plan_summary(tl, name)

    was_running = store.is_live and app.ensure_closed(allow_quit)
    folder = store.folder(name)
    folder.mkdir(parents=True)
    try:
        draft = schema.empty_draft(tl.width, tl.height, tl.fps, store.platform())
        _populate(_ctx(store, name, draft), tl)
        now = time.time_ns() // 1000
        atomic_write_json(folder / PRIMARY, draft)
        atomic_write_json(folder / "draft_meta_info.json", _meta(name, folder, draft, now))
        _cover(tl, folder / "draft_cover.jpg")
        store.register(_registry_entry(name, folder, draft, now))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)   # never leave a half-built draft
        raise
    if was_running and relaunch:
        app.launch()
    return summary(draft)


def rebuild_draft(tl: Timeline, name: str, store: DraftStore | None = None,
                  allow_quit: bool = False, dry_run: bool = False, relaunch: bool = True,
                  replace: bool = False, reason: str = "rebuild from plan") -> dict:
    """Write a timeline INTO an existing draft, replacing its timeline. The
    draft keeps its id, name and Media panel; the old timeline is backed up
    first (`restore` undoes it). A draft that already has clips on its
    timeline is only replaced with replace=True, so hand edits aren't lost
    by accident."""
    store = store or DraftStore()
    problems = tl.validate()
    if problems:
        raise DraftError("timeline is not buildable:\n  " + "\n  ".join(problems))
    current = store.load(name)
    used = sum(len(t["segments"]) for t in current["tracks"])
    if used and not replace:
        raise DraftError(f"{name!r} already has {used} segment(s) on its timeline. Pass "
                         "replace=True (--replace) to rebuild it; the current version is "
                         "backed up first and `restore` brings it back.")
    if dry_run:
        return _plan_summary(tl, name, replaces=summary(current) if used else None)

    was_running = store.is_live and app.ensure_closed(allow_quit)
    current = store.load(name)                  # re-read: quitting CapCut may have saved it
    draft = schema.empty_draft(tl.width, tl.height, tl.fps,
                               current.get("platform") or store.platform())
    draft["id"] = current["id"]
    draft["name"] = current.get("name", "")
    _populate(_ctx(store, name, draft), tl)
    snap = store.save(name, draft, reason)
    _cover(tl, store.folder(name) / "draft_cover.jpg")
    if was_running and relaunch:
        app.launch()
    out = summary(draft)
    out["backup"] = str(snap) if snap else None
    return out


# ------------------------------------------------------------------ editing


def edit(name: str, mutate: Callable[[dict, _Ctx], object], reason: str,
         store: DraftStore | None = None, allow_quit: bool = False,
         dry_run: bool = False, relaunch: bool = True) -> dict:
    """Additive edit of an EXISTING draft. Loads it as it stands (so hand
    edits saved by CapCut are kept), applies `mutate`, checks, backs up, writes.

    With dry_run the mutation runs on a copy and nothing touches disk, though
    media staging is skipped too — so dry runs report structure, not paths."""
    store = store or DraftStore()
    current = store.load(name)
    if dry_run:
        draft = copy.deepcopy(current)

        class _DryStore(DraftStore):
            def stage_media(self, _name, src):
                p = Path(src).expanduser().resolve()
                if not p.exists():
                    raise DraftError(f"media not found: {p}")
                return p

        mutate(draft, _ctx(_DryStore(store.root, store.backups), name, draft))
        schema.repair(draft)
        return {"dry_run": True, "before": summary(current), "after": summary(draft),
                "problems": schema.check(draft)}

    was_running = store.is_live and app.ensure_closed(allow_quit)
    draft = store.load(name)   # re-read: quitting CapCut may have just saved it
    mutate(draft, _ctx(store, name, draft))
    schema.repair(draft)
    problems = schema.check(draft)
    if problems:
        raise DraftError("edit would corrupt the draft, nothing written:\n  "
                         + "\n  ".join(problems))
    snap = store.save(name, draft, reason)
    if was_running and relaunch:
        app.launch()
    out = summary(draft)
    out["backup"] = str(snap) if snap else None
    return out


def apply_grade(draft: dict, ctx: _Ctx, g: dict[str, float] | list[dict[str, float]],
                roles: tuple[str, ...] = ("main",)) -> int:
    """Replace the Adjust grade on every clip of the given roles. Sliders not
    in `g` are reset, other effects (beauty, filters) are left alone."""
    from ..grade import SLIDERS
    effects = draft["materials"].setdefault("effects", [])
    is_adjust = {e["id"] for e in effects
                 if e.get("effect_id") == schema.ADJUST_EFFECT_ID and e.get("type") in SLIDERS}
    n = 0
    for role in roles:
        for i, (_, seg) in enumerate(segments(draft, role)):
            look = g[i] if isinstance(g, list) else g    # a list = one grade per clip
            seg["extra_material_refs"] = [r for r in seg["extra_material_refs"]
                                          if r not in is_adjust]
            seg["extra_material_refs"] += schema.adjust_materials(draft["materials"], look,
                                                                  ctx.bundle())
            seg["enable_adjust"] = True
            n += 1
    still = {r for t in draft["tracks"] for s in t["segments"]
             for r in s.get("extra_material_refs", [])}
    draft["materials"]["effects"] = [e for e in draft["materials"]["effects"]
                                     if e["id"] not in is_adjust or e["id"] in still]
    return n


def restyle(draft: dict, ctx: _Ctx, tl: Timeline) -> dict:
    """Bring an EXISTING draft's look in line with a plan without rebuilding
    it: per-shot grades and keyframes onto the main clips (matched by order),
    plus letterbox bars. Anything else in the draft is left as it is."""
    main = segments(draft, "main")
    if len(main) != len(tl.clips):
        raise DraftError(f"draft has {len(main)} main clips, plan has {len(tl.clips)} — "
                         "restyle matches them by order, so they must line up")
    apply_grade(draft, ctx, [tl.clip_grade(c) for c in tl.clips])
    mats = {m["id"]: m for m in draft["materials"]["videos"]}
    cw, ch = draft["canvas_config"]["width"], draft["canvas_config"]["height"]
    for (_, seg), c in zip(main, tl.clips):
        m = mats.get(seg["material_id"], {})
        base = c.zoom * fill_scale(SimpleNamespace(width=m.get("width"),
                                                   height=m.get("height")), cw, ch)
        transform(seg, scale=base, x=0.0, y=0.0, rotation=0.0, opacity=1.0)
        set_motion(ctx, seg, c.keyframes, base)

    def is_bar(s):
        return mats.get(s["material_id"], {}).get("material_name", "").startswith("letterbox-")
    while True:                     # drop old bars (indices shift after each removal)
        idx = next((i for i, (_, s) in enumerate(segments(draft, "overlay")) if is_bar(s)),
                   None)
        if idx is None:
            break
        remove(draft, "overlay", idx)
    if tl.letterbox:
        end = max(s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                  for _, s in main)
        add_letterbox(ctx, tl.letterbox, end)
    return summary(draft)


def transform(seg: dict, scale=None, x=None, y=None, rotation=None, opacity=None) -> None:
    clip = seg.get("clip")
    if clip is None:
        raise DraftError("this segment has no visual transform (audio?)")
    if scale is not None:
        clip["scale"] = {"x": scale, "y": scale}
        seg["uniform_scale"] = {"on": True, "value": scale}
    if x is not None:
        clip["transform"]["x"] = x
    if y is not None:
        clip["transform"]["y"] = y
    if rotation is not None:
        clip["rotation"] = rotation
    if opacity is not None:
        clip["alpha"] = opacity


def remove(draft: dict, role: str, index: int) -> dict:
    """Drop one segment plus any materials only it referenced."""
    trk, seg = pick(draft, role, index)
    trk["segments"].remove(seg)
    used = {s["material_id"] for t in draft["tracks"] for s in t["segments"]}
    used |= {r for t in draft["tracks"] for s in t["segments"]
             for r in s.get("extra_material_refs", [])}
    gone = {seg["material_id"], *seg.get("extra_material_refs", [])} - used
    for cat, rows in draft["materials"].items():
        draft["materials"][cat] = [m for m in rows
                                   if not (isinstance(m, dict) and m.get("id") in gone)]
    if not trk["segments"] and role != "main":
        draft["tracks"].remove(trk)
    return seg
