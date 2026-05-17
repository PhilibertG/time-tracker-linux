#!/usr/bin/env python3
"""tt — time tracker CLI.

Usage:
  tt today          Show today's sessions grouped by app
  tt current        Show currently active window/session
  tt week           Show this week's summary
  tt app <name>     Show sessions for a specific app today
  tt json           Dump today's raw sessions as JSON
  tt help           Show this help
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

DATA_DIR = Path.home() / ".local" / "share" / "timetracker"
SESSIONS_FILE = DATA_DIR / "sessions.json"
CURRENT_FILE = DATA_DIR / "current.json"

# ANSI colours
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def c(text, code):
    return f"\033[{code}m{text}{RESET}"


def rgb(text, r, g, b):
    return f"\033[38;2;{r};{g};{b}m{text}{RESET}"


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


APP_COLORS = {
    "vscode": "#007acc", "vscodium": "#2b7bba", "cursor": "#8855ff",
    "neovim": "#57a143", "vim": "#019733", "firefox": "#ff7139",
    "chrome": "#4285f4", "chromium": "#4285f4", "terminal": "#4caf50",
    "python": "#3572a5", "pycharm": "#21d789", "intellij": "#fe315d",
    "code": "#007acc",
}


def app_color_rgb(name: str):
    key = name.lower().replace(" ", "").replace("-", "")
    for k, v in APP_COLORS.items():
        if k in key or key in k:
            return hex_to_rgb(v)
    h = hash(name) & 0xFFFFFF
    r, g, b = (h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF
    avg = (r + g + b) // 3
    return (
        min(255, r // 2 + avg // 2 + 60),
        min(255, g // 2 + avg // 2 + 60),
        min(255, b // 2 + avg // 2 + 60),
    )


def colored_app(name: str) -> str:
    r, g, b = app_color_rgb(name)
    return rgb(name, r, g, b)


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m = seconds // 60
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m"


def session_duration(s: dict) -> float:
    try:
        start = datetime.fromisoformat(s["start"])
        end = (
            datetime.fromisoformat(s["end"])
            if s.get("end")
            else datetime.now().astimezone()
        )
        return max(0.0, (end - start).total_seconds())
    except Exception:
        return 0.0


def load_all_sessions() -> list:
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return []


def sessions_for_day(day: date) -> list:
    prefix = day.isoformat()
    return [s for s in load_all_sessions() if s.get("start", "").startswith(prefix)]


def load_current() -> dict | None:
    try:
        data = json.loads(CURRENT_FILE.read_text())
        return data if data else None
    except Exception:
        return None


def inject_current(sessions: list, current: dict | None) -> list:
    if not current or not current.get("app"):
        return sessions
    return sessions + [
        {
            "app": current["app"],
            "context": current.get("context", ""),
            "start": current["start"],
            "end": None,
        }
    ]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_current():
    cur = load_current()
    if not cur or not cur.get("app"):
        print(c("No active session detected.", "2"))
        return
    try:
        start = datetime.fromisoformat(cur["start"])
        elapsed = (datetime.now().astimezone() - start).total_seconds()
        dur = fmt_duration(elapsed)
    except Exception:
        dur = "?"
    print(
        f"{colored_app(cur['app'])}  "
        + c(cur.get("context", ""), "2")
        + f"  {c(dur, '33')}"
    )
    if cur.get("title"):
        print(c(f"  ↳ {cur['title'][:100]}", "2"))
    if cur.get("method"):
        print(c(f"  detection: {cur['method']}", "2"))


def cmd_today():
    today = date.today()
    sessions = sessions_for_day(today)
    current = load_current()
    sessions = inject_current(sessions, current)

    if not sessions:
        print(c("No sessions today.", "2"))
        return

    by_app: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for s in sessions:
        by_app[s["app"]][s.get("context", "")].append(s)

    day_total = sum(session_duration(s) for s in sessions)
    print(f"{BOLD}Today  {today.strftime('%a %d %b')}{RESET}  —  {c(fmt_duration(day_total), '33')}")
    print()

    for app_name, contexts in sorted(by_app.items(), key=lambda kv: -sum(session_duration(s) for sl in kv[1].values() for s in sl)):
        app_total = sum(session_duration(s) for sl in contexts.values() for s in sl)
        print(f"  {colored_app(app_name)}  {c(fmt_duration(app_total), '33')}")

        for ctx, ctx_sessions in sorted(contexts.items(), key=lambda kv: -sum(session_duration(s) for s in kv[1])):
            ctx_total = sum(session_duration(s) for s in ctx_sessions)
            is_live = any(s.get("end") is None for s in ctx_sessions)
            live_tag = c(" ●LIVE", "31") if is_live else ""
            label = ctx or c("(no context)", "2")
            print(f"    {c(label, '0')}  {c(fmt_duration(ctx_total), '2')}{live_tag}")

    print()


def cmd_week():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    print(f"{BOLD}Week of {monday.strftime('%d %b %Y')}{RESET}")
    print()

    week_total = 0.0
    by_app_week: dict[str, float] = defaultdict(float)

    for i in range(7):
        day = monday + timedelta(days=i)
        if day > today:
            break
        sessions = sessions_for_day(day)
        if day == today:
            sessions = inject_current(sessions, load_current())
        if not sessions:
            continue
        day_total = sum(session_duration(s) for s in sessions)
        week_total += day_total
        for s in sessions:
            by_app_week[s["app"]] += session_duration(s)

        by_app_day: dict[str, float] = defaultdict(float)
        for s in sessions:
            by_app_day[s["app"]] += session_duration(s)

        apps_str = "  ".join(
            f"{colored_app(a)} {c(fmt_duration(t), '2')}"
            for a, t in sorted(by_app_day.items(), key=lambda kv: -kv[1])[:4]
        )
        print(f"  {BOLD}{day.strftime('%a %d')}{RESET}  {c(fmt_duration(day_total), '33')}  {apps_str}")

    print()
    print(f"  {BOLD}Total{RESET}  {c(fmt_duration(week_total), '33')}")
    print()
    print(f"  {BOLD}By app{RESET}")
    for app_name, total in sorted(by_app_week.items(), key=lambda kv: -kv[1]):
        bar_len = int(total / max(week_total, 1) * 30)
        bar = "█" * bar_len
        r, g, b = app_color_rgb(app_name)
        print(f"    {colored_app(app_name):<30} {c(fmt_duration(total), '33'):>8}  {rgb(bar, r, g, b)}")
    print()


def cmd_app(name: str):
    today = date.today()
    sessions = sessions_for_day(today)
    current = load_current()
    sessions = inject_current(sessions, current)

    name_lower = name.lower()
    matched = [s for s in sessions if name_lower in s["app"].lower()]
    if not matched:
        print(c(f"No sessions for '{name}' today.", "2"))
        return

    total = sum(session_duration(s) for s in matched)
    print(f"{colored_app(name)}  {c(fmt_duration(total), '33')}")
    print()

    by_ctx: dict[str, list] = defaultdict(list)
    for s in matched:
        by_ctx[s.get("context", "")].append(s)

    for ctx, ctx_sessions in sorted(by_ctx.items(), key=lambda kv: -sum(session_duration(s) for s in kv[1])):
        ctx_total = sum(session_duration(s) for s in ctx_sessions)
        is_live = any(s.get("end") is None for s in ctx_sessions)
        live_tag = c(" ●LIVE", "31") if is_live else ""
        label = ctx or c("(no context)", "2")
        print(f"  {label}  {c(fmt_duration(ctx_total), '2')}{live_tag}")
        for s in sorted(ctx_sessions, key=lambda x: x["start"]):
            start = s["start"][11:16]
            end = s["end"][11:16] if s.get("end") else "now "
            dur = fmt_duration(session_duration(s))
            print(f"    {c(start, '2')}–{c(end, '2')}  {c(dur, '33')}")
    print()


def cmd_json():
    today = date.today()
    sessions = sessions_for_day(today)
    current = load_current()
    sessions = inject_current(sessions, current)
    print(json.dumps(sessions, indent=2, ensure_ascii=False))


def cmd_help():
    print(__doc__)


COMMANDS = {
    "today": cmd_today,
    "current": cmd_current,
    "week": cmd_week,
    "json": cmd_json,
    "help": cmd_help,
}


def main():
    args = sys.argv[1:]
    if not args:
        cmd_today()
        return

    cmd = args[0].lower()

    if cmd == "app":
        if len(args) < 2:
            print("Usage: tt app <name>")
            sys.exit(1)
        cmd_app(args[1])
    elif cmd in COMMANDS:
        COMMANDS[cmd]()
    else:
        print(f"Unknown command: {cmd}")
        cmd_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
