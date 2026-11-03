import json
import subprocess
from pathlib import Path

import pytest

from capcut_forge.capcut import app


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("media")
    # 4s talking-head stand-in: tone, silence 1.5-2.5s, tone.
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
       "-f", "lavfi", "-i",
       "aevalsrc='if(between(t,1.5,2.5),0,0.5*sin(440*2*PI*t))':s=48000:d=4",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
       str(d / "talk.mp4"))
    ff("-f", "lavfi", "-i", "sine=frequency=220:duration=2", str(d / "music.wav"))
    ff("-f", "lavfi", "-i", "color=c=red@0.5:size=200x100,format=rgba", "-frames:v", "1",
       str(d / "badge.png"))
    return d


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fake CapCut draft root + backup dir; never touches the real app."""
    from capcut_forge.capcut.store import DraftStore
    root = tmp_path / "drafts"
    root.mkdir()
    (root / "root_meta_info.json").write_text(json.dumps(
        {"all_draft_store": [], "draft_ids": 0, "root_path": str(root)}), encoding="utf-8")
    monkeypatch.setattr(app, "is_running", lambda: False)
    monkeypatch.setattr(app, "launch", lambda: None)
    return DraftStore(root, tmp_path / "backups")


@pytest.fixture
def words():
    return [
        {"word": "So", "start": 0.0, "end": 0.2},
        {"word": "um", "start": 0.25, "end": 0.5},
        {"word": "this", "start": 0.55, "end": 0.8},
        {"word": "is", "start": 0.85, "end": 1.0},
        {"word": "claud", "start": 1.05, "end": 1.4},
        {"word": "code.", "start": 1.45, "end": 1.5},
        {"word": "It", "start": 2.6, "end": 2.8},
        {"word": "edits", "start": 2.85, "end": 3.2},
        {"word": "videos.", "start": 3.25, "end": 3.8},
    ]
