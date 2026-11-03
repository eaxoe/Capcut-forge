"""Safe read/write access to CapCut's draft folder.

Every write is atomic (temp file + rename) and preceded by a backup kept
OUTSIDE CapCut's own tree, so nothing capcut-forge does is unrecoverable:
`capcut-forge capcut restore <draft>` puts the last state back.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

WINDOWS = sys.platform == "win32"


def _user_data() -> Path:
    """CapCut's own data folder: ~/Movies/CapCut on a Mac,
    %LOCALAPPDATA%\\CapCut on Windows."""
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") \
            / "CapCut/User Data"
    return Path.home() / "Movies/CapCut/User Data"


USER_DATA = _user_data()
REGISTRY = "root_meta_info.json"
# A draft's timeline file: CapCut for Mac writes draft_info.json, CapCut for
# Windows draft_content.json (some versions keep both, in step). New drafts
# get this platform's name; existing ones keep whichever they have.
DRAFT_FILES = ("draft_info.json", "draft_content.json")
PRIMARY = "draft_content.json" if WINDOWS else "draft_info.json"


def _capcut_root() -> Path:
    """Where CapCut keeps drafts. Users can move it in CapCut's settings, which
    records the choice as `currentCustomDraftPath` in Config/globalSetting."""
    try:
        text = (USER_DATA / "Config/globalSetting").read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.startswith("currentCustomDraftPath="):
                path = Path(line.split("=", 1)[1].strip())
                if path.is_dir():
                    return path
    except OSError:
        pass
    return USER_DATA / "Projects/com.lveditor.draft"


DEFAULT_ROOT = _capcut_root()


def forge_home() -> Path:
    """Backups, plans and caches: the platform's per-user app-data folder."""
    if os.environ.get("CAPCUT_FORGE_HOME"):
        return Path(os.environ["CAPCUT_FORGE_HOME"])
    if WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") \
            / "capcut-forge"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/capcut-forge"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") \
        / "capcut-forge"


class DraftError(RuntimeError):
    pass


def atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.capcut-forge-tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def read_draft_json(path: Path) -> dict:
    """Parse a draft file, telling an encrypted one (JianYing, the Chinese
    edition, encrypts its drafts) apart from a damaged one."""
    raw = path.read_bytes()
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        if not raw.lstrip().startswith(b"{"):
            raise DraftError(f"{path.name} in {path.parent.name} is encrypted (JianYing and "
                             "some CapCut builds do this), so it can't be edited. Render an "
                             "MP4 instead, or use CapCut's international edition.") from None
        raise DraftError(f"{path} is damaged (not valid JSON)") from None


def _key(name: str) -> str:
    """Loose form of a name for matching: case, accents, spaces, dashes and
    underscores don't matter ("test y" finds "TestY", "ete" finds "Été").
    Also evens out macOS's decomposed Unicode in file names."""
    flat = "".join(c for c in unicodedata.normalize("NFKD", name)
                   if not unicodedata.combining(c))
    return re.sub(r"[\s_\-—–]+", "", flat).casefold()


class DraftStore:
    def __init__(self, root: Path | None = None, backups: Path | None = None):
        self.root = Path(root or os.environ.get("CAPCUT_FORGE_DRAFT_ROOT", DEFAULT_ROOT))
        self.backups = Path(backups or forge_home() / "backups")
        self._bundle: str | None = None

    @property
    def is_live(self) -> bool:
        """True when this is the folder the CapCut app itself owns, so writes
        must wait for the app to be closed."""
        try:
            return self.root.resolve() == DEFAULT_ROOT.resolve()
        except OSError:
            return False

    # ------------------------------------------------------------ reading

    def folder(self, name: str) -> Path:
        if not name or any(c in name for c in '/\\:') or name.startswith("."):
            raise DraftError(f"invalid draft name: {name!r}")
        return self.root / name

    def draft_files(self, name: str) -> list[Path]:
        """The timeline file(s) a draft has; [] when it isn't a draft."""
        folder = self.folder(name)
        return [folder / f for f in DRAFT_FILES if (folder / f).exists()]

    def draft_file(self, name: str) -> Path:
        """The file to read a draft from (this platform's name for a new one)."""
        files = self.draft_files(name)
        return files[0] if files else self.folder(name) / PRIMARY

    def exists(self, name: str) -> bool:
        return bool(self.draft_files(name))

    def load(self, name: str) -> dict:
        path = self.draft_file(name)
        if not path.exists():
            raise DraftError(f"no such draft: {name} ({self.folder(name)})")
        return read_draft_json(path)

    def _drafts(self):
        """Every draft file in the root (one per draft)."""
        seen = set()
        for f in DRAFT_FILES:
            for p in self.root.glob(f"*/{f}"):
                if p.parent.name not in seen and not p.parent.name.startswith("."):
                    seen.add(p.parent.name)
                    yield p

    def registry(self) -> dict:
        path = self.root / REGISTRY
        if not path.exists():
            return {"all_draft_store": [], "draft_ids": 0, "root_path": str(self.root)}
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        return self.registry().get("all_draft_store", [])

    def names(self) -> list[str]:
        """Draft folder names, registry order first (newest edit first), then
        any folder CapCut hasn't registered."""
        seen = [Path(d.get("draft_fold_path") or d.get("draft_name", "")).name
                for d in self.list()]
        seen = [n for n in seen if n and self._is_draft(self.root / n)]
        extra = sorted(p.parent.name for p in self._drafts())
        return list(dict.fromkeys(seen + extra))

    def resolve(self, ref: str) -> str:
        """Draft folder name for what a person typed: the exact name, a path to
        the draft folder, or a loose / partial match on the name."""
        ref = str(ref).strip().strip("'\"")
        p = Path(ref).expanduser()
        if p.is_absolute() and self._is_draft(p):
            if p.parent.resolve() != self.root.resolve():
                raise DraftError(f"{p} is a draft outside the draft folder {self.root}")
            return p.name
        names = self.names()
        if ref in names:
            return ref
        for test in (lambda n: _key(n) == _key(ref),
                     lambda n: _key(n).startswith(_key(ref)),
                     lambda n: _key(ref) in _key(n)):
            hits = [n for n in names if test(n)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                exact = [n for n in hits if n.casefold() == ref.casefold()]
                if len(exact) == 1:
                    return exact[0]
                raise DraftError(f"{ref!r} matches several drafts: {', '.join(hits)}")
        raise DraftError(f"no CapCut draft called {ref!r}. Drafts: {', '.join(names) or 'none'}")

    def expand(self, name: str, path: str) -> str:
        """A media path as CapCut stores it -> a real path. CapCut 9 writes
        files inside the draft as `##_draftpath_placeholder_<id>_##/...` on
        the timeline and as `./...` in the Media panel list."""
        folder = str(self.folder(name))
        path = re.sub(r"^##_draftpath_placeholder_[^#]*_##", lambda _: folder, path)
        if path.startswith(("./", ".\\")):
            path = str(Path(folder) / path[2:])
        return path

    @staticmethod
    def _is_draft(folder: Path) -> bool:
        return any((folder / f).exists() for f in DRAFT_FILES)

    def meta(self, name: str) -> dict:
        path = self.folder(name) / "draft_meta_info.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def bin_media(self, name: str) -> list[dict]:
        """What was imported into the draft's Media panel (not necessarily on
        the timeline): [{path, kind, duration, exists}], in import order."""
        out = []
        for group in self.meta(name).get("draft_materials", []):
            if group.get("type") != 0:            # 0 = local media; others are covers/subdrafts
                continue
            for m in group.get("value", []):
                path = self.expand(name, m.get("file_Path") or "")
                kind = {"video": "video", "photo": "image", "music": "audio",
                        "audio": "audio"}.get(m.get("metetype", ""))
                if not path or kind is None:
                    continue
                out.append({"path": path, "kind": kind,
                            "duration": (m.get("duration") or 0) / 1e6,
                            "exists": Path(path).exists(),
                            "imported": m.get("import_time_ms", 0)})
        out.sort(key=lambda m: m["imported"])
        return out

    def platform(self) -> dict:
        """Lift the platform block from an existing local draft so new drafts
        read as native. Stays on this machine."""
        for p in sorted(self._drafts(), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                return read_draft_json(p)["platform"]
            except (KeyError, DraftError, OSError):
                continue
        return {"os": "windows" if WINDOWS else "mac", "os_version": "", "app_id": 359289,
                "app_version": "9.3.0", "app_source": "cc", "device_id": "",
                "hard_disk_id": "", "mac_address": ""}

    def adjust_bundle(self) -> str:
        """Where this CapCut install keeps its Adjust-panel effect. Taken from
        the newest local draft that used it, else the effect cache, else the
        copy inside the app bundle."""
        from .schema import ADJUST_EFFECT_ID
        if self._bundle:
            return self._bundle
        for p in sorted(self._drafts(), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                if ADJUST_EFFECT_ID not in text:     # skip the JSON parse of big drafts
                    continue
                effects = json.loads(text)["materials"].get("effects", [])
            except (KeyError, json.JSONDecodeError, OSError):
                continue
            for e in effects:
                if e.get("effect_id") == ADJUST_EFFECT_ID and e.get("path"):
                    self._bundle = e["path"]
                    return self._bundle
        for cache in (USER_DATA / "Cache/effect" / ADJUST_EFFECT_ID,
                      Path.home() / "Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut"
                      / "User Data/Cache/effect" / ADJUST_EFFECT_ID):
            dirs = sorted((d for d in cache.glob("*")
                           if d.is_dir() and not d.name.endswith("_tmp")),
                          key=lambda d: d.stat().st_mtime, reverse=True) if cache.exists() else []
            if dirs:
                self._bundle = str(dirs[0])
                return self._bundle
        if WINDOWS:          # no fixed install path; CapCut finds it by effect_id
            return ""
        return ("/Applications/CapCut.app/Contents/MacOS/../Resources/"
                "DefaultAdjustBundle/merge_all_adjust_color")

    # ------------------------------------------------------------ backups

    def backup(self, name: str, reason: str) -> Path | None:
        """Snapshot a draft's JSON and MOVE its Timelines/ cache aside.

        Moving the cache is also what makes edits stick: CapCut only re-imports
        draft_info.json when Timelines/ is absent, otherwise it trusts its own
        cached state and silently ignores the file."""
        folder = self.folder(name)
        if not folder.exists():
            return None
        now = time.time()        # one clock reading, so stamps sort in time order
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now % 1 * 1e6):06d}"
        dest = self.backups / name / stamp
        while dest.exists():
            dest = dest.with_name(dest.name + "x")
        dest.mkdir(parents=True)
        for f in (*DRAFT_FILES, "draft_meta_info.json"):
            if (folder / f).exists():
                shutil.copy2(folder / f, dest / f)
        if (folder / "Timelines").exists():
            shutil.move(str(folder / "Timelines"), dest / "Timelines")
        (dest / "reason.txt").write_text(reason, encoding="utf-8")
        return dest

    def backup_registry(self) -> None:
        src = self.root / REGISTRY
        if src.exists():
            dest = self.backups / "_registry"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / f"{time.strftime('%Y%m%d-%H%M%S')}-"
                                     f"{time.time_ns() % 1_000_000:06d}-{REGISTRY}")

    def list_backups(self, name: str) -> list[Path]:
        d = self.backups / name
        return sorted(d.iterdir()) if d.exists() else []

    def restore(self, name: str, which: str | None = None) -> Path:
        snaps = self.list_backups(name)
        if not snaps:
            raise DraftError(f"no backups for {name}")
        snap = next((s for s in snaps if s.name == which), None) if which else snaps[-1]
        if snap is None:
            raise DraftError(f"no backup {which!r} for {name}")
        self.backup(name, f"before restoring {snap.name}")
        folder = self.folder(name)
        for f in (*DRAFT_FILES, "draft_meta_info.json"):
            if (snap / f).exists():
                shutil.copy2(snap / f, folder / f)
        if (snap / "Timelines").exists():
            shutil.copytree(snap / "Timelines", folder / "Timelines")
        self.touch(name, self.load(name))
        return snap

    # ------------------------------------------------------------ writing

    def timelines(self, name: str) -> list[str]:
        """Live timeline ids of a draft (CapCut 9 lets one draft hold several)."""
        path = self.folder(name) / "Timelines/project.json"
        try:
            proj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [t["id"] for t in proj.get("timelines", []) if not t.get("is_marked_delete")]

    def save(self, name: str, draft: dict, reason: str) -> Path | None:
        """Write an EXISTING draft back, after a backup. Caller must have
        made sure CapCut is closed."""
        if len(self.timelines(name)) > 1:
            raise DraftError(f"{name} has {len(self.timelines(name))} timelines; capcut-forge "
                             "only edits single-timeline drafts (it would drop the others)")
        targets = self.draft_files(name) or [self.draft_file(name)]
        snap = self.backup(name, reason)
        for path in targets:                     # keep Windows' twin files in step
            atomic_write_json(path, draft)
            path.with_name(path.name + ".bak").unlink(missing_ok=True)
        self.touch(name, draft)
        return snap

    def touch(self, name: str, draft: dict) -> None:
        """Bring the duration and modified time CapCut shows on its home
        screen (meta file + registry) in line with the draft just written."""
        now = time.time_ns() // 1000
        meta_path = self.folder(name) / "draft_meta_info.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta.update(tm_duration=draft.get("duration", 0), tm_draft_modified=now)
            atomic_write_json(meta_path, meta)
        reg = self.registry()
        folder = self.folder(name)
        for d in reg.get("all_draft_store", []):
            if d.get("draft_fold_path") and Path(d["draft_fold_path"]) == folder:
                d.update(tm_duration=draft.get("duration", 0), tm_draft_modified=now)
                self.backup_registry()
                atomic_write_json(self.root / REGISTRY, reg)
                break

    def create(self, name: str, draft: dict, meta: dict, registry_entry: dict) -> Path:
        folder = self.folder(name)
        if folder.exists():
            raise DraftError(f"draft folder already exists: {folder} (pick another name)")
        folder.mkdir(parents=True)
        (folder / "Resources").mkdir()
        atomic_write_json(folder / PRIMARY, draft)
        atomic_write_json(folder / "draft_meta_info.json", meta)
        self.register(registry_entry)
        return folder

    def register(self, entry: dict) -> None:
        self.backup_registry()
        reg = self.registry()
        reg.setdefault("all_draft_store", []).insert(0, entry)
        reg["draft_ids"] = reg.get("draft_ids", 0) + 1
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.root / REGISTRY, reg)

    def stage_media(self, name: str, src: str | Path) -> Path:
        """Hardlink (zero bytes, same volume) or copy media into the draft's
        Resources/. CapCut's sandbox reliably reads its own draft tree, so
        footage elsewhere can show up as red 'File not accessible' clips."""
        src = Path(src).expanduser().resolve()
        if not src.exists():
            raise DraftError(f"media not found: {src}")
        res = self.folder(name) / "Resources"
        res.mkdir(parents=True, exist_ok=True)
        if src.parent == res:
            return src
        dst = res / src.name
        if dst.exists():
            a, b = dst.stat(), src.stat()
            if dst.samefile(src) or (a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns):
                return dst
            dst = res / f"{src.stem}-{time.time_ns() % 100000}{src.suffix}"
        try:
            dst.hardlink_to(src)
        except OSError:
            shutil.copy2(src, dst)
        return dst
