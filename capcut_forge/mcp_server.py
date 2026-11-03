"""capcut-forge as MCP tools, so Claude (or any MCP client) can edit with you.

Run:  uv run --extra mcp capcut-forge-mcp      (connector name: capcut-forge,
      see .mcp.json.example)

Safety: `capcut_auto_edit` writes straight into the draft (that is its job),
after a backup, and refuses to replace a timeline that already has clips
unless replace=True. The lower-level write tools default to a dry run and
only write with apply=True. No tool quits CapCut unless quit_app=True.
Nothing here talks to the network or spends money.
"""
from __future__ import annotations

import functools
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import ambient, auto, cuts, grade, media, styles, transcribe
from .capcut import app
from .capcut import build as cc
from .capcut.store import DraftError, DraftStore
from .plan import PlanOptions, plan
from .render.ffmpeg import RenderError
from .timeline import AudioTrack, Caption, Overlay, Timeline

INSTRUCTIONS = """\
capcut-forge edits CapCut projects on this computer (macOS or Windows).

Typical request: "edit my CapCut project TestY" or "make a video from the
clips in ~/Downloads/trip". Call capcut_auto_edit with project=<name> (loose
names work: "testy" finds "TestY") and/or folder=<path>. It finds the footage
(the project's Media panel, the folder, or both), decides the edit (talking
head with captions, or a montage cut to the music), and writes it straight
into that CapCut project. A folder with no matching project becomes a new
project named after the folder.

- CapCut must be closed while a project is written, or CapCut overwrites the
  change when it quits. If the tool says CapCut is open, ask the user, then
  retry with quit_app=true (CapCut saves its work on a normal quit and is
  reopened afterwards).
- A project whose timeline already has clips is only replaced with
  replace=true. Ask first. Every write is backed up; capcut_restore undoes it.
- Use capcut_projects to see what exists, capcut_project_media to see what
  footage a project holds, capcut_inspect for its timeline.
- Fine edits afterwards: capcut_add (text, overlay, audio), capcut_grade.
"""

server = MCPServer("capcut-forge", instructions=INSTRUCTIONS)

# Failures we expect (CapCut open, no such draft, bad file...) carry a message
# the model needs to act on; MCP only passes the text through for ToolError.
EXPECTED = (DraftError, app.AppRunningError, media.MediaError, transcribe.TranscribeError,
            RenderError, ValueError, KeyError, OSError)


def tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EXPECTED as e:
            raise ToolError(str(e)) from e
    return server.tool()(wrapper)


def _store() -> DraftStore:
    return DraftStore()


def _name(store: DraftStore, draft: str) -> str:
    return store.resolve(draft)


@tool
def probe(path: str) -> dict:
    """Duration, size, fps and stream info for a media file."""
    i = media.probe(path)
    return {"duration": i.duration, "width": i.width, "height": i.height, "fps": i.fps,
            "has_video": i.has_video, "has_audio": i.has_audio}


@tool
def transcribe_video(path: str, model: str = "small", language: str | None = None) -> dict:
    """Local Whisper transcript with word timings; saved next to the video."""
    t = transcribe.transcribe(path, model, language)
    out = transcribe.save(t, Path(path).with_suffix(".transcript.json"))
    return {"transcript": str(out), "words": len(t["words"]), "language": t["language"],
            "preview": " ".join(w["word"] for w in t["words"][:60])}


@tool
def make_plan(video: str, transcript: str | None = None, cuts_file: str | None = None,
              cut: str = "auto", max_gap: float = 0.5, drop_retakes: bool = False,
              caption_style: str = "tiktok-raw", hook_text: str | None = None,
              grade_preset: str | None = None, ambient_mood: str | None = None,
              ambient_volume: float = 0.35,
              punch_in: float = 1.0, music: str | None = None, music_volume: float = 0.15,
              width: int = 1080, height: int = 1920, out: str | None = None) -> dict:
    """Raw video -> edit plan JSON (cuts, captions, hook, zooms, music).
    caption_style: one of the presets, or 'none'. grade_preset: soft-cinematic,
    warm-film, moody-teal, clean-bright. ambient_mood: warm, dark, airy, dreamy
    (an original bed is synthesised locally)."""
    opts = PlanOptions(cut=cut, max_gap=max_gap, drop_retakes=drop_retakes,
                       caption_style=None if caption_style == "none" else caption_style,
                       hook_text=hook_text, punch_in=punch_in, music=music,
                       grade=grade_preset, ambient=ambient_mood,
                       ambient_volume=ambient_volume,
                       music_volume=music_volume, width=width, height=height)
    logs: list[str] = []
    tl, _ = plan(video, opts, transcribe.load(transcript) if transcript else None,
                 cuts.load(cuts_file) if cuts_file else None, log=logs.append)
    path = tl.save(out or Path(video).with_suffix(".plan.json"))
    return {"plan": str(path), "clips": len(tl.clips), "captions": len(tl.captions),
            "duration": round(tl.duration, 2), "log": logs}


@tool
def read_plan(plan_path: str) -> dict:
    """The full edit plan, for review or hand-tuning before rendering."""
    return Timeline.load(plan_path).to_dict()


@tool
def render_plan(plan_path: str, out: str | None = None, until: float | None = None) -> str:
    """Render a plan to MP4 with ffmpeg (no CapCut needed). `until` = preview length."""
    from .render.ffmpeg import render
    return str(render(Timeline.load(plan_path), out or Path(plan_path).with_suffix(".mp4"),
                      until))


@tool
def caption_styles() -> dict:
    """Available caption looks."""
    return {k: {"size": s.size, "color": s.color, "box": s.box_color,
                "stroke": s.stroke_width, "y": s.y_frac} for k, s in styles.PRESETS.items()}


@tool
def capcut_projects() -> dict:
    """Every CapCut project on this Mac: name, timeline length, how much
    footage its Media panel holds, and whether CapCut is open right now."""
    store = _store()
    rows = []
    for n in store.names():
        try:
            d = store.load(n)
        except Exception:  # noqa: BLE001 - a broken draft shouldn't hide the rest
            continue
        bin_items = store.bin_media(n)
        modified = store.draft_file(n).stat().st_mtime
        rows.append({"name": n, "timeline_seconds": round(d.get("duration", 0) / 1e6, 2),
                     "timeline_empty": not any(t["segments"] for t in d["tracks"]),
                     "media": {k: sum(1 for m in bin_items if m["kind"] == k and m["exists"])
                               for k in ("video", "image", "audio")},
                     "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified))})
    return {"capcut_open": app.is_running(), "draft_folder": str(store.root), "projects": rows}


@tool
def capcut_list() -> list[dict]:
    """Drafts in CapCut's registry (see capcut_projects for more detail)."""
    return [{"name": d["draft_name"], "seconds": d.get("tm_duration", 0) / 1e6}
            for d in _store().list()]


@tool
def capcut_project_media(project: str) -> dict:
    """The footage, photos and music imported into a project's Media panel."""
    store = _store()
    name = _name(store, project)
    return {"project": name, "media": [
        {"file": m["path"], "kind": m["kind"], "seconds": round(m["duration"], 2),
         "exists": m["exists"]} for m in store.bin_media(name)]}


@tool
def capcut_inspect(draft: str) -> dict:
    """Tracks and segments of a CapCut draft (loose names work)."""
    store = _store()
    return cc.summary(store.load(_name(store, draft)))


@tool
def capcut_auto_edit(project: str | None = None, folder: str | None = None,
                     new_name: str | None = None, style: str = "auto",
                     aspect: str = "auto", max_seconds: float | None = None,
                     music: str | None = None, look: str = "auto",
                     captions: str = "tiktok-raw", hook: str | None = None,
                     title: str | None = None, broll: bool = True,
                     replace: bool = False, quit_app: bool = False,
                     preview_only: bool = False, render_to: str | None = None) -> dict:
    """Edit a CapCut project automatically and write it straight into CapCut.

    project: the CapCut project name (loose match), or a path to a draft.
    folder: a folder of footage/music, used alone (becomes a project named
      after the folder, or `new_name`) or added to `project`'s media.
    style: auto | talking (captions, cut pauses/fillers) | montage (cut to music).
    aspect: auto | 9:16 | 16:9 | 1:1 | 4:5 | 3:4 | 4:3.
    max_seconds: cap the length (montages default to 60s; talking keeps it all).
    music: a music file to use instead of what's in the project.
    look: auto | none | cinematic | soft-cinematic | warm-film | moody-teal | clean-bright.
    captions: caption style (tiktok-raw, hook, explainer, punch) or 'none'.
    hook / title: text card over the opening seconds.
    replace: needed when the project's timeline already has clips (it is backed up).
    quit_app: allow quitting CapCut (it saves on quit) so the project can be written.
    preview_only: decide and plan, but write nothing.
    render_to: also render an MP4 of the edit to this path."""
    logs: list[str] = []
    out = auto.auto_edit(project, folder, new_name, style, aspect, max_seconds, music,
                         None if look == "none" else look,
                         None if captions == "none" else captions, hook, title, broll,
                         replace=replace, allow_quit=quit_app, dry_run=preview_only,
                         render=render_to, log=logs.append)
    out["log"] = logs
    if not preview_only:
        out["undo"] = f"capcut_restore(draft={out['draft']!r})" if not out["new_draft"] else \
            "delete the new project in CapCut"
    return out


@tool
def capcut_build(plan_path: str, name: str, apply: bool = False,
                 quit_app: bool = False, into_existing: bool = False,
                 replace: bool = False) -> dict:
    """Create a NEW CapCut draft from a plan, or with into_existing=True write
    the plan into an existing draft. Dry run unless apply=True."""
    tl = Timeline.load(plan_path)
    if into_existing:
        store = _store()
        return cc.rebuild_draft(tl, _name(store, name), store, allow_quit=quit_app,
                                dry_run=not apply, replace=replace)
    return cc.build_draft(tl, name, allow_quit=quit_app, dry_run=not apply)


@tool
def capcut_add(draft: str, kind: str, media_or_text: str, at: float,
               duration: float | None = None, style: str = "tiktok-raw",
               volume: float = 1.0, layer: int = 1, apply: bool = False,
               quit_app: bool = False) -> dict:
    """Add to an existing draft. kind: overlay | text | audio. Dry run unless
    apply=True; every real write is backed up first (see capcut_restore)."""
    if kind == "overlay":
        ov = Overlay(source=str(Path(media_or_text).resolve()), at=at, duration=duration,
                     layer=layer)
        mutate = lambda d, ctx: cc.add_overlay(ctx, ov)  # noqa: E731
    elif kind == "text":
        cap = Caption(text=media_or_text, start=at, end=at + (duration or 3.0), style=style,
                      role="hook" if style == "hook" else "caption")
        mutate = lambda d, ctx: cc.add_caption(ctx, cap)  # noqa: E731
    elif kind == "audio":
        tr = AudioTrack(source=str(Path(media_or_text).resolve()), at=at, duration=duration,
                        volume=volume)
        mutate = lambda d, ctx: cc.add_audio(ctx, tr, 0)  # noqa: E731
    else:
        raise ValueError("kind must be overlay, text or audio")
    store = _store()
    return cc.edit(_name(store, draft), mutate, f"mcp add {kind}@{at}", store,
                   allow_quit=quit_app, dry_run=not apply)


@tool
def capcut_grade(draft: str, preset: str | None = None, sliders: dict | None = None,
                 tracks: str = "main", apply: bool = False, quit_app: bool = False) -> dict:
    """Colour-grade a draft with CapCut's own Adjust sliders (editable in the app).
    sliders: e.g. {"temperature": 0.1, "contrast": 0.05}, each -1..1 (UI = x50).
    Dry run unless apply=True."""
    g = grade.resolve(preset, **(sliders or {}))
    store = _store()
    return cc.edit(_name(store, draft),
                   lambda d, ctx: cc.apply_grade(d, ctx, g, tuple(tracks.split(","))),
                   f"mcp grade {preset or 'custom'}", store, allow_quit=quit_app,
                   dry_run=not apply)


@tool
def make_ambient(out: str, duration: float, mood: str = "warm") -> str:
    """Synthesise an original, royalty-free ambient bed (warm|dark|airy|dreamy)."""
    return str(ambient.generate(out, duration, mood))


@tool
def capcut_restore(draft: str, backup: str | None = None, quit_app: bool = False) -> dict:
    """Undo capcut-forge's last write to a draft (or roll back to a named backup).
    CapCut must be closed, or pass quit_app=True."""
    store = _store()
    name = _name(store, draft)
    was_running = store.is_live and app.ensure_closed(quit_app)
    snap = store.restore(name, backup)
    if was_running:
        app.launch()
    return {"restored": name, "from_backup": snap.name,
            "reason": (snap / "reason.txt").read_text(encoding="utf-8") if (snap / "reason.txt").exists() else ""}


@tool
def capcut_backups(draft: str) -> list[dict]:
    """capcut-forge's backups of a draft, oldest first."""
    store = _store()
    return [{"backup": b.name,
             "reason": (b / "reason.txt").read_text(encoding="utf-8") if (b / "reason.txt").exists() else ""}
            for b in store.list_backups(_name(store, draft))]


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
