"""Live lane: drive the OPEN CapCut editor through the macOS accessibility tree.

CapCut's QML UI exposes ByteDance's internal automation IDs (PlayerPlayBtn,
MTLSVideoP:<clip>, currentProgress|HH:MM:SS:FF, ...). We find elements by
those names and click their centres with synthesized CGEvents. The IDs are
undocumented test hooks — re-check with `capcut-forge live dump` after updates.

Needs:  uv sync --extra live   and Accessibility permission for your terminal
(System Settings > Privacy & Security > Accessibility). `shot` also needs
Screen Recording.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from . import app

try:
    from ApplicationServices import (AXUIElementCopyAttributeValue,
                                     AXUIElementCreateApplication,
                                     AXIsProcessTrusted, AXValueGetValue,
                                     kAXValueCGPointType, kAXValueCGSizeType)
    from Quartz import (CGEventCreateKeyboardEvent, CGEventCreateMouseEvent, CGEventPost,
                        CGEventSetFlags, CGEventSetIntegerValueField,
                        CGWindowListCopyWindowInfo, kCGEventFlagMaskAlternate,
                        kCGEventFlagMaskCommand, kCGEventFlagMaskShift,
                        kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventMouseMoved,
                        kCGHIDEventTap, kCGMouseButtonLeft, kCGMouseEventClickState,
                        kCGNullWindowID, kCGWindowListOptionOnScreenOnly)
    HAVE_AX = True
except ImportError:   # pragma: no cover - depends on the optional extra
    HAVE_AX = False


class LiveError(RuntimeError):
    pass


KEYCODES = {"space": 49, "return": 36, "escape": 53, "delete": 51, "tab": 48,
            "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119,
            "a": 0, "b": 11, "c": 8, "e": 14, "m": 46, "s": 1, "v": 9, "z": 6}

# Export dialog controls, as offsets from the window CENTRE. The dialog is a
# fixed-size QML overlay with no AX internals, so position is the only way in.
# Measured on CapCut 8.9 — verify with `capcut-forge live shot` after updates.
EXPORT_BTN_OFFSET = (308, 302)

_fronted = False


def _need():
    if not HAVE_AX:
        if sys.platform != "darwin":
            raise LiveError("the live lane drives CapCut through macOS accessibility, so it "
                            "is macOS only; the file lane (capcut, auto) works on Windows")
        raise LiveError("live lane needs the extra: uv sync --extra live")
    if not AXIsProcessTrusted():
        raise LiveError("this terminal lacks Accessibility permission (System Settings > "
                        "Privacy & Security > Accessibility)")
    if not app.is_running():
        raise LiveError("CapCut is not running (capcut-forge live launch)")


def _attr(el, name):
    err, val = AXUIElementCopyAttributeValue(el, name, None)
    return val if err == 0 else None


def _geom(el):
    pos, size = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if pos is None or size is None:
        return None
    ok, p = AXValueGetValue(pos, kAXValueCGPointType, None)
    ok2, s = AXValueGetValue(size, kAXValueCGSizeType, None)
    return (p.x, p.y, s.width, s.height) if ok and ok2 else None


def elements(needle: str | None = None, max_depth: int = 18):
    """[(element, name, (x, y, w, h) | None)] across all CapCut windows."""
    _need()
    out = []

    def walk(el, depth):
        if depth > max_depth:
            return
        name = _attr(el, "AXTitle") or _attr(el, "AXDescription") or ""
        if needle is None or needle.lower() in name.lower():
            out.append((el, name, _geom(el)))
        for kid in _attr(el, "AXChildren") or []:
            walk(kid, depth + 1)

    for w in _attr(AXUIElementCreateApplication(app.pid()), "AXWindows") or []:
        walk(w, 0)
    return out


def find(needle: str, timeout: float = 0):
    t0 = time.time()
    while True:
        hits = [h for h in elements(needle) if h[2]]
        if hits:
            return hits[0]
        if time.time() - t0 >= timeout:
            return None
        time.sleep(0.5)


def _front():
    """Clicks AND keys need CapCut frontmost: otherwise macOS spends the first
    click activating the window and the app never sees it."""
    global _fronted
    if not _fronted:
        app.activate()
        time.sleep(0.4)
        _fronted = True


def click_at(x: float, y: float, clicks: int = 1) -> None:
    _front()
    CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x, y),
                                                        kCGMouseButtonLeft))
    time.sleep(0.05)
    for n in range(1, clicks + 1):
        for kind in (kCGEventLeftMouseDown, kCGEventLeftMouseUp):
            e = CGEventCreateMouseEvent(None, kind, (x, y), kCGMouseButtonLeft)
            CGEventSetIntegerValueField(e, kCGMouseEventClickState, n)
            CGEventPost(kCGHIDEventTap, e)
            time.sleep(0.05)


def click(needle: str, clicks: int = 1, timeout: float = 5) -> str:
    hit = find(needle, timeout)
    if hit is None:
        raise LiveError(f"element not found: {needle}")
    _, name, (x, y, w, h) = hit
    click_at(x + w / 2, y + h / 2, clicks)
    return name


def key(combo: str, times: int = 1) -> None:
    _front()
    parts = combo.lower().split("+")
    k = parts[-1]
    if k not in KEYCODES:
        raise LiveError(f"unknown key {k!r} (known: {', '.join(sorted(KEYCODES))})")
    flags = 0
    for mod, mask in (("cmd", kCGEventFlagMaskCommand), ("shift", kCGEventFlagMaskShift),
                      ("alt", kCGEventFlagMaskAlternate)):
        if mod in parts:
            flags |= mask
    for i in range(times):
        for down in (True, False):
            e = CGEventCreateKeyboardEvent(None, KEYCODES[k], down)
            if flags:
                CGEventSetFlags(e, flags)
            CGEventPost(kCGHIDEventTap, e)
            time.sleep(0.03)
        if i + 1 < times:
            time.sleep(0.03)   # CapCut drops frame steps sent back to back


# ------------------------------------------------------------------ playhead


def playhead() -> tuple[str, str]:
    def read(n):
        h = find(n)
        return (_attr(h[0], "AXDescription") or "").split("|")[-1] if h else "?"
    return read("currentProgress"), read("totalProgress")


def _tc_seconds(tc: str, fps: float) -> float | None:
    try:
        h, m, s, f = (int(x) for x in tc.split(":"))
    except ValueError:
        return None
    return h * 3600 + m * 60 + s + f / fps


def clips():
    """Timeline clips left-to-right as (name, x, y, w, h)."""
    return sorted(((n, *g) for _, n, g in elements("MTLSVideoP") if g),
                  key=lambda c: (c[2], c[1]))


def _ruler_y() -> float:
    hit = find("cutoff") or find("undo")
    if hit is None:
        raise LiveError("timeline toolbar not found (is a draft open?)")
    return hit[2][1] + 33


def seek(seconds: float, fps: float = 30.0, tolerance_frames: int = 0) -> float | None:
    """Frame-exact seek: calibrate seconds<->pixels from two ruler clicks read
    back off the app's own playhead, click the mapped x, then close the
    remaining error with paced arrow-key frame steps."""
    root = find("MainTimeLineRoot")
    if root is None:
        raise LiveError("timeline not found (is a draft open?)")
    rx, _, rw, _ = root[2]
    left, right = rx + 180, rx + rw - 20
    cl = clips()
    if cl:
        right = min(right, max(c[1] + c[3] for c in cl))
        left = max(left, min(c[1] for c in cl))
    if right - left < 80:
        left, right = rx + 180, rx + rw - 20
    y = _ruler_y()
    xa, xb = left + (right - left) * 0.30, left + (right - left) * 0.75
    click_at(xa, y); time.sleep(0.35)
    ta = _tc_seconds(playhead()[0], fps)
    click_at(xb, y); time.sleep(0.35)
    tb = _tc_seconds(playhead()[0], fps)
    if ta is None or tb is None or abs(tb - ta) < 1e-6:
        raise LiveError("calibration failed: the playhead did not move. An onboarding "
                        "tooltip over the ruler swallows clicks — run `capcut-forge live shot`.")
    spp = (tb - ta) / (xb - xa)
    click_at(xa + (seconds - ta) / spp, y); time.sleep(0.35)
    for _ in range(8):
        now = _tc_seconds(playhead()[0], fps)
        if now is None:
            break
        err = round((seconds - now) * fps)
        if abs(err) <= tolerance_frames:
            return now
        key("right" if err > 0 else "left", min(abs(err), 400))
        time.sleep(0.3)
    return _tc_seconds(playhead()[0], fps)


def select(index: int) -> str:
    cl = clips()
    if not 0 <= index < len(cl):
        raise LiveError(f"clip {index} out of range (timeline has {len(cl)})")
    name, x, y, w, h = cl[index]
    click_at(x + w / 2, y + h / 2)
    time.sleep(0.5)
    return name


# ------------------------------------------------------------------ app-level


def open_draft(name: str) -> str:
    if not app.is_running():
        app.launch()
        time.sleep(4)
    _need()
    app.activate()
    title = f"HomePageDraftTitle:{name}"
    hit = find(title, timeout=15)
    if hit is None:
        key("escape")   # a promo modal can cover the home screen on launch
        hit = find(title, timeout=15)
    if hit is None:
        raise LiveError(f"draft tile not found on the home screen: {name}")
    time.sleep(1.5)     # the window repositions after launch; re-find before clicking
    hit = find(title, timeout=10) or hit
    tx, ty = hit[2][0], hit[2][1]
    tile = next((g for _, n, g in elements("HomePageDraft")
                 if n == "HomePageDraft" and g and g[0] <= tx <= g[0] + g[2] and g[1] <= ty),
                None)
    x, y = (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2) if tile else (tx, ty - 60)
    click_at(x, y, clicks=2)
    if find("MainTimeLineRoot", timeout=25) is None:
        raise LiveError("editor did not open")
    return playhead()[1]


def window_id() -> int | None:
    best = None
    for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
        if w.get("kCGWindowOwnerName") != app.APP:
            continue
        b = w["kCGWindowBounds"]
        area = b["Width"] * b["Height"]
        if best is None or area > best[1]:
            best = (w["kCGWindowNumber"], area)
    return best[0] if best else None


def shot(out: str | Path = "capcut.png") -> Path:
    _need()
    wid = window_id()
    if wid is None:
        raise LiveError("no CapCut window on screen")
    out = Path(out).expanduser().resolve()
    subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), str(out)], check=True)
    return out


def export(out_dir: str | Path | None = None, timeout: float = 900) -> Path:
    """Drive the export dialog and wait for the file to stop growing.

    Never touches "Sync exported videos to space" (cloud upload) and dismisses
    the post-export share screen with Escape — it is modal, blocks the next
    export, and clicking through it publishes."""
    _need()
    folder = Path(out_dir).expanduser() if out_dir else Path.home() / "Downloads"
    before = {p: p.stat().st_mtime for p in folder.glob("*.mp4")}
    key("escape")
    time.sleep(1.0)
    win = next((g for _, n, g in elements("CapCut") if g and g[2] > 1000), None)
    if win is None:
        raise LiveError("could not locate the CapCut window")
    cx, cy = win[0] + win[2] / 2, win[1] + win[3] / 2
    click("MainWindowTitleBarExportBtn")
    time.sleep(6)       # the dialog paints well after the click returns
    click_at(cx + EXPORT_BTN_OFFSET[0], cy + EXPORT_BTN_OFFSET[1])
    t0, last, stable, target = time.time(), -1, 0, None
    while time.time() - t0 < timeout:
        time.sleep(4)
        fresh = [p for p in folder.glob("*.mp4")
                 if p not in before or p.stat().st_mtime > before[p]]
        if not fresh:
            continue
        target = max(fresh, key=lambda p: p.stat().st_mtime)
        size = target.stat().st_size
        stable = stable + 1 if size == last and size > 0 else 0
        last = size
        if stable >= 2:
            break
    key("escape")
    if target is None:
        raise LiveError("export produced no file — the dialog may still be open; "
                        "check EXPORT_BTN_OFFSET with `capcut-forge live shot`")
    return target
