#!/usr/bin/env python3
"""Time tracker daemon — polls active window every 2s, logs sessions."""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "timetracker"
SESSIONS_FILE = DATA_DIR / "sessions.json"
CURRENT_FILE = DATA_DIR / "current.json"
CLIENT_FILE = DATA_DIR / "active_client.json"

POLL_INTERVAL = 2       # seconds between polls
IDLE_THRESHOLD = 30     # seconds gap → new session (same app)
IDLE_PAUSE = 300        # seconds of system idle → pause tracking


# ---------------------------------------------------------------------------
# Window detection — tries methods in order, uses first that works
# ---------------------------------------------------------------------------

def _try_window_calls_extended():
    """Extension 'Window Calls Extended' (GNOME ext id 4974) via DBus."""
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Shell",
                "--object-path", "/org/gnome/Shell/Extensions/WindowCallsExtended",
                "--method", "org.gnome.Shell.Extensions.WindowCallsExtended.List",
            ],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        raw = result.stdout.strip()
        focus_match = re.search(r"'focus':\s*<true>", raw)
        if not focus_match:
            return None
        title_match = re.search(r"'title':\s*<'([^']*)'>[^}]*'focus':\s*<true>", raw)
        if not title_match:
            title_match = re.search(r"'focus':\s*<true>[^}]*'title':\s*<'([^']*)'>", raw)
        class_match = re.search(r"'wmclass':\s*<'([^']*)'>[^}]*'focus':\s*<true>", raw)
        if not class_match:
            class_match = re.search(r"'focus':\s*<true>[^}]*'wmclass':\s*<'([^']*)'>", raw)
        title = title_match.group(1) if title_match else ""
        wmclass = class_match.group(1) if class_match else ""
        return {"title": title, "wmclass": wmclass, "method": "window-calls-extended"}
    except Exception:
        return None


def _try_gnome_shell_eval():
    try:
        script = (
            "let w=global.display.focus_window;"
            "w ? w.get_wm_class()+' ||| '+w.get_title() : ''"
        )
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Shell",
                "--object-path", "/org/gnome/Shell",
                "--method", "org.gnome.Shell.Eval",
                script,
            ],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return None
        m = re.match(r"\(true,\s*'(.+)'\)", result.stdout.strip())
        if not m:
            return None
        parts = m.group(1).split(" ||| ", 1)
        wmclass = parts[0].strip()
        title = parts[1].strip() if len(parts) > 1 else ""
        if not wmclass:
            return None
        return {"title": title, "wmclass": wmclass, "method": "gnome-eval"}
    except Exception:
        return None


def _try_xdotool():
    try:
        wid_result = subprocess.run(
            ["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=2
        )
        if wid_result.returncode != 0:
            return None
        wid = wid_result.stdout.strip()
        name_result = subprocess.run(
            ["xdotool", "getwindowname", wid], capture_output=True, text=True, timeout=2
        )
        class_result = subprocess.run(
            ["xdotool", "getwindowclassname", wid],
            capture_output=True, text=True, timeout=2,
        )
        title = name_result.stdout.strip() if name_result.returncode == 0 else ""
        wmclass = class_result.stdout.strip() if class_result.returncode == 0 else ""
        if not title and not wmclass:
            return None
        return {"title": title, "wmclass": wmclass, "method": "xdotool"}
    except Exception:
        return None


def _try_wnck():
    try:
        import gi
        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck, GLib
        screen = Wnck.Screen.get_default()
        if screen is None:
            return None
        ctx = GLib.MainContext.default()
        while ctx.pending():
            ctx.iteration(False)
        screen.force_update()
        window = screen.get_active_window()
        if window is None:
            return None
        return {
            "title": window.get_name() or "",
            "wmclass": window.get_class_group_name() or "",
            "method": "wnck",
        }
    except Exception:
        return None


_active_method = None


def get_active_window():
    global _active_method
    methods = [_try_window_calls_extended, _try_gnome_shell_eval, _try_xdotool, _try_wnck]
    if _active_method:
        result = _active_method()
        if result:
            return result
        _active_method = None
    for method in methods:
        result = method()
        if result:
            _active_method = method
            return result
    return None


def get_idle_seconds() -> float:
    """Return seconds of system-level input idle time, 0 if unavailable."""
    try:
        result = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            return int(result.stdout.strip()) / 1000.0
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Title parsing
# ---------------------------------------------------------------------------

TITLE_RULES = [
    (r"^(.+?)\s+[–—-]\s+(.+?)\s+[–—-]\s+(?:Visual Studio Code|VSCodium|Cursor)", "VSCode", 1),
    (r"^(?:Neo[Vv]im|VIM?):\s+(.+)", "Neovim", 1),
    (r"^VIM\s+(.+)", "Vim", 1),
    (r"^(.+?)\s+[–—-]\s+(?:Mozilla Firefox|Google Chrome|Chromium)", None, 1),
    (r"^(.+?)\s*[-–]\s*(?:GNOME Terminal|Terminal|Konsole|kitty|Alacritty)", "Terminal", 1),
    (r"^(.+?)\s+\[(.+?)\]\s+[–—-]\s+(?:IntelliJ|PyCharm|CLion|WebStorm|Rider|GoLand)", None, 2),
    (r"^(.+?)\s+[–—-]\s+(\S+)\s*$", None, 1),
]


def parse_title(title: str, wmclass: str) -> tuple[str, str]:
    app_from_class = wmclass.split(".")[-1].strip() if wmclass else ""
    if app_from_class:
        app_from_class = app_from_class[0].upper() + app_from_class[1:]
    for pattern, app_override, ctx_group in TITLE_RULES:
        m = re.match(pattern, title, re.IGNORECASE)
        if m:
            app = app_override or app_from_class or wmclass
            try:
                context = m.group(ctx_group).strip()
            except IndexError:
                context = ""
            return app, context
    return app_from_class or wmclass or "Unknown", title[:80] if title else ""


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------

def read_active_client() -> str:
    try:
        return json.loads(CLIENT_FILE.read_text()).get("client", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def load_sessions() -> list:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except Exception:
            return []
    return []


def save_sessions(sessions: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2))


def write_current(info: dict | None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_FILE.write_text(json.dumps(info or {}, indent=2))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    sessions = load_sessions()

    current_app = None
    current_context = None
    current_client = None
    current_start = None
    last_seen = None
    was_idle = False

    print(f"[timetracker] daemon started, writing to {DATA_DIR}")

    while True:
        idle_secs = get_idle_seconds()
        now = time.time()

        if idle_secs >= IDLE_PAUSE:
            if not was_idle and current_app and current_start:
                sessions.append({
                    "app": current_app,
                    "context": current_context,
                    "client": current_client or "",
                    "start": current_start,
                    "end": now_iso(),
                })
                save_sessions(sessions)
                current_app = None
                current_context = None
                current_client = None
                current_start = None
            was_idle = True
            write_current({"idle": True, "idle_seconds": int(idle_secs)})
            time.sleep(POLL_INTERVAL)
            continue

        if was_idle:
            was_idle = False
            last_seen = None  # reset gap detection after idle

        win = get_active_window()
        active_client = read_active_client()

        if win:
            app, context = parse_title(win["title"], win["wmclass"])

            client_changed = active_client != current_client
            app_changed = app != current_app or context != current_context
            gap_break = last_seen is not None and (now - last_seen) > IDLE_THRESHOLD

            if app_changed or client_changed or gap_break:
                if current_app and current_start:
                    sessions.append({
                        "app": current_app,
                        "context": current_context,
                        "client": current_client or "",
                        "start": current_start,
                        "end": now_iso(),
                    })
                    save_sessions(sessions)

                current_app = app
                current_context = context
                current_client = active_client
                current_start = now_iso()

            last_seen = now
            write_current({
                "app": current_app,
                "context": current_context,
                "client": current_client,
                "start": current_start,
                "title": win["title"],
                "method": win["method"],
                "updated": now_iso(),
                "idle": False,
            })
        else:
            write_current(None)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
