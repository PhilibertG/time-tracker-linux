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
POLL_INTERVAL = 2  # seconds
IDLE_THRESHOLD = 30  # seconds before a gap creates a new session


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
        # Output: "([{'id': <uint64 …>, 'title': '…', 'wmclass': '…', 'focus': true, …}],)\n"
        raw = result.stdout.strip()
        # Extract focused window
        focus_match = re.search(r"'focus':\s*<true>", raw)
        if not focus_match:
            return None
        # Find the dict containing focus: true — grab title and wmclass
        # Split on }, { to get individual window dicts
        title_match = re.search(r"'title':\s*<'([^']*)'>[^}]*'focus':\s*<true>", raw)
        if not title_match:
            # title might come after focus
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
    """Classic gdbus eval (works only when not blocked by GNOME policy)."""
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
    """xdotool — works under XWayland, not pure Wayland."""
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


def _try_ydotool():
    """ydotool — Wayland-native input tool (limited window query support)."""
    try:
        # ydotool does not expose window queries directly; skip
        return None
    except Exception:
        return None


def _try_wnck():
    """libwnck via python3-wnck — only works with XWayland."""
    try:
        import gi
        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck, GLib

        screen = Wnck.Screen.get_default()
        if screen is None:
            return None
        # Force screen update
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


# Detection strategy cache
_active_method = None


def get_active_window():
    """Return dict with title, wmclass, method — or None."""
    global _active_method

    methods = [
        _try_window_calls_extended,
        _try_gnome_shell_eval,
        _try_xdotool,
        _try_wnck,
    ]

    # If a method worked before, try it first
    if _active_method:
        result = _active_method()
        if result:
            return result
        # Method stopped working — re-probe
        _active_method = None

    for method in methods:
        result = method()
        if result:
            _active_method = method
            return result

    return None


# ---------------------------------------------------------------------------
# Title parsing — extract app name + context (file/project)
# ---------------------------------------------------------------------------

# (pattern, app_name, context_group)  — context_group=None means use wmclass
TITLE_RULES = [
    # VSCode / Cursor / VSCodium:  "filename — folder — Code"
    (r"^(.+?)\s+[–—-]\s+(.+?)\s+[–—-]\s+(?:Visual Studio Code|VSCodium|Cursor)", "VSCode", 1),
    # Neovim terminal title:  "NeoVim: filename"
    (r"^(?:Neo[Vv]im|VIM?):\s+(.+)", "Neovim", 1),
    # Vim in terminal
    (r"^VIM\s+(.+)", "Vim", 1),
    # Firefox / Chrome:  "Page Title — Mozilla Firefox"
    (r"^(.+?)\s+[–—-]\s+(?:Mozilla Firefox|Google Chrome|Chromium)", None, 1),
    # Terminal (GNOME Terminal / Konsole / Kitty / Alacritty)
    (r"^(.+?)\s*[-–]\s*(?:GNOME Terminal|Terminal|Konsole|kitty|Alacritty)", "Terminal", 1),
    # JetBrains IDEs:  "filename [project] — IDE"
    (r"^(.+?)\s+\[(.+?)\]\s+[–—-]\s+(?:IntelliJ|PyCharm|CLion|WebStorm|Rider|GoLand)", None, 2),
    # Generic "Something — AppName"
    (r"^(.+?)\s+[–—-]\s+(\S+)\s*$", None, 1),
]


def parse_title(title: str, wmclass: str) -> tuple[str, str]:
    """Return (app_name, context) from window title + wmclass."""
    # Normalise wmclass to something readable
    app_from_class = wmclass.split(".")[-1].strip() if wmclass else ""
    # Capitalise first letter
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

    # Fallback: no match — use wmclass as app, title as context
    return app_from_class or wmclass or "Unknown", title[:80] if title else ""


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
    current_start = None
    last_seen = None  # epoch of last successful poll

    print(f"[timetracker] daemon started, writing to {DATA_DIR}")

    while True:
        win = get_active_window()
        now = time.time()

        if win:
            app, context = parse_title(win["title"], win["wmclass"])
            changed = (app != current_app or context != current_context)

            # Gap too long → treat as new session even if same app
            if last_seen and (now - last_seen) > IDLE_THRESHOLD:
                changed = True

            if changed:
                # Close previous session
                if current_app and current_start:
                    sessions.append(
                        {
                            "app": current_app,
                            "context": current_context,
                            "start": current_start,
                            "end": now_iso(),
                        }
                    )
                    save_sessions(sessions)

                current_app = app
                current_context = context
                current_start = now_iso()

            last_seen = now
            write_current(
                {
                    "app": current_app,
                    "context": current_context,
                    "start": current_start,
                    "title": win["title"],
                    "method": win["method"],
                    "updated": now_iso(),
                }
            )
        else:
            # No window detected — clear current but keep session open
            write_current(None)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
