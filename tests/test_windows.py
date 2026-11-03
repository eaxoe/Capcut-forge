"""Windows behaviour, simulated so it runs on any OS (CI also runs the whole
suite on a real Windows machine)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from capcut_forge import auto
from capcut_forge.capcut import app, build as cc, schema, store as store_mod
from capcut_forge.capcut.store import DraftError, DraftStore


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPCUT_FORGE_HOME", str(tmp_path / "home"))


@pytest.fixture
def win_store(store, monkeypatch):
    """A draft root laid out the way CapCut for Windows writes it."""
    monkeypatch.setattr(store_mod, "PRIMARY", "draft_content.json")
    monkeypatch.setattr(cc, "PRIMARY", "draft_content.json")
    return store


def _win_draft(store, name, media=(), both=False):
    folder = store.root / name
    folder.mkdir()
    d = schema.empty_draft(1920, 1080, 30.0, {"os": "windows", "app_version": "9.5.0"})
    for f in (("draft_content.json", "draft_info.json") if both else ("draft_content.json",)):
        (folder / f).write_text(json.dumps(d), encoding="utf-8")
    meta = {"draft_name": name, "draft_materials": [{"type": 0, "value": [
        {"file_Path": str(p), "metetype": "video" if p.suffix == ".mp4" else "music",
         "duration": 1} for p in media]}]}
    (folder / "draft_meta_info.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


def test_windows_draft_is_found_loaded_and_written(win_store, media_dir):
    folder = _win_draft(win_store, "Holiday", [media_dir / "talk.mp4"])
    assert win_store.resolve("holiday") == "Holiday"
    assert win_store.draft_file("Holiday").name == "draft_content.json"
    auto.auto_edit("holiday", store=win_store, style="montage", log=lambda *_: None)
    assert not (folder / "draft_info.json").exists()           # no Mac file invented
    d = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
    assert d["tracks"] and schema.check(d) == []
    assert (win_store.list_backups("Holiday")[0] / "draft_content.json").exists()


def test_windows_twin_files_stay_in_step(win_store, media_dir):
    folder = _win_draft(win_store, "Twin", [media_dir / "talk.mp4"], both=True)
    auto.auto_edit("twin", store=win_store, style="montage", log=lambda *_: None)
    a = (folder / "draft_content.json").read_text(encoding="utf-8")
    assert a == (folder / "draft_info.json").read_text(encoding="utf-8")
    win_store.restore("Twin")
    assert not json.loads(a)["tracks"] == win_store.load("Twin")["tracks"]


def test_new_windows_project_uses_draft_content(win_store, media_dir, tmp_path):
    clips = tmp_path / "Trip"
    clips.mkdir()
    shutil.copy(media_dir / "talk.mp4", clips / "a.mp4")
    auto.auto_edit(str(clips), store=win_store, style="montage", log=lambda *_: None)
    assert (win_store.root / "Trip" / "draft_content.json").exists()
    assert win_store.list()[0]["draft_json_file"].endswith("draft_content.json")


def test_encrypted_draft_explains(win_store):
    folder = win_store.root / "Locked"
    folder.mkdir()
    (folder / "draft_content.json").write_bytes(b"\x8f\x02binary-blob\x00")
    with pytest.raises(DraftError, match="encrypted"):
        win_store.load("Locked")


def test_unicode_names_and_captions(store, media_dir):
    """Windows' default text encoding is cp1252; every file must be UTF-8."""
    name = "Été — 夏休み"
    folder = store.root / name
    folder.mkdir()
    d = schema.empty_draft(1080, 1920, 30.0, {})
    (folder / "draft_info.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    from capcut_forge.timeline import Caption
    cc.edit(name, lambda d, ctx: cc.add_caption(ctx, Caption("café — ça va? 夏", 0, 1)),
            "x", store)
    assert "café — ça va? 夏" in schema.text_content(store.load(name)["materials"]["texts"][0])
    assert store.resolve("ete") == name


def test_windows_paths_rejected_in_names(store):
    for bad in ("a\\b", "C:x", "../up"):
        with pytest.raises(DraftError):
            store.folder(bad)


def test_windows_relative_media_paths(store):
    store.root.joinpath("P").mkdir()
    assert store.expand("P", ".\\Resources\\a.mp4") == str(store.root / "P" / "Resources\\a.mp4")


def test_windows_locations(monkeypatch, tmp_path):
    monkeypatch.delenv("CAPCUT_FORGE_HOME")
    monkeypatch.setattr(store_mod, "WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert store_mod.forge_home() == tmp_path / "Roaming" / "capcut-forge"
    assert store_mod._user_data() == tmp_path / "Local" / "CapCut" / "User Data"


def test_windows_process_control(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "WINDOWS", True)
    calls = []
    running = ['"CapCut.exe","4242","Console","1","512,000 K"']

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "taskkill":
            running.clear()
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(running), stderr="")
    monkeypatch.setattr(app.subprocess, "run", fake_run)
    assert app.pid() == 4242
    app.quit()
    assert ["taskkill", "/IM", "CapCut.exe"] in calls          # a close request, never /F
    assert not any("/F" in c for c in calls)
    assert app.pid() is None
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "CapCut" / "Apps" / "CapCut.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert app.executable() == exe
