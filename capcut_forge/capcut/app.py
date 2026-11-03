"""CapCut process lifecycle on macOS and Windows."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

APP = "CapCut"
WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"


class AppRunningError(RuntimeError):
    pass


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")


def executable() -> Path | None:
    """Where CapCut is installed, or None. Windows installs per user under
    %LOCALAPPDATA%\\CapCut\\Apps (a launcher, plus one folder per version)."""
    if WINDOWS:
        apps = _local_appdata() / "CapCut/Apps"
        for p in [apps / "CapCut.exe", *sorted(apps.glob("*/CapCut.exe"), reverse=True)]:
            if p.exists():
                return p
        return None
    for p in (Path("/Applications/CapCut.app"), Path.home() / "Applications/CapCut.app"):
        if p.exists():
            return p
    return None


def pid() -> int | None:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {APP}.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in out.stdout.splitlines():      # "CapCut.exe","1234","Console",...
            cells = [c.strip('"') for c in line.split('","')]
            if len(cells) > 1 and cells[0].lower() == f"{APP.lower()}.exe":
                return int(cells[1])
        return None
    if not MAC:
        return None                               # no CapCut on other systems
    out = subprocess.run(["pgrep", "-x", APP], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return int(out.stdout.split()[0]) if out.returncode == 0 and out.stdout.strip() else None


def is_running() -> bool:
    return pid() is not None


def quit(timeout: float = 30) -> None:
    """Ask CapCut to quit normally, so it saves its own state first. On
    Windows that is taskkill WITHOUT /F: a close request, like clicking X."""
    if not is_running():
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/IM", f"{APP}.exe"], capture_output=True, check=False)
    else:
        subprocess.run(["osascript", "-e", f'tell application "{APP}" to quit'], check=False)
    t0 = time.time()
    while is_running():
        if time.time() - t0 > timeout:
            raise AppRunningError(f"CapCut did not quit within {timeout:.0f}s — "
                                  "close it yourself (check for an unsaved-changes dialog)")
        time.sleep(0.5)


def launch() -> None:
    if WINDOWS:
        exe = executable()
        if exe is None:
            raise AppRunningError("can't find CapCut.exe under %LOCALAPPDATA%\\CapCut\\Apps; "
                                  "open CapCut yourself")
        os.startfile(exe)                          # noqa: S606 - the user's own CapCut
    elif MAC:
        subprocess.run(["open", "-a", APP], check=True)


def activate() -> None:
    if MAC:
        subprocess.run(["osascript", "-e", f'tell application "{APP}" to activate'], check=False)


def ensure_closed(allow_quit: bool) -> bool:
    """CapCut rewrites its draft registry on quit, clobbering anything written
    while it was open — so every file write needs it closed. Returns whether it
    was running (so the caller can relaunch). Never quits without permission:
    the user may have work open."""
    if not is_running():
        return False
    if not allow_quit:
        raise AppRunningError("CapCut is open. Close it, or pass --quit-app to let "
                              "capcut-forge quit it (CapCut saves on a normal quit).")
    quit()
    return True
