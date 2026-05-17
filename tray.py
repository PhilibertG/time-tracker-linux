#!/usr/bin/env python3
"""
System tray icon for the time tracker.

On GNOME Wayland without the AppIndicator extension, QSystemTrayIcon is
invisible.  This script detects that case and falls back to a small
floating window pinned to the bottom-right corner.
"""

import json
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

try:
    from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
    from PyQt6.QtGui import QColor, QPainter, QPixmap, QFont, QIcon, QCursor
    from PyQt6.QtWidgets import (
        QApplication,
        QLabel,
        QMainWindow,
        QMenu,
        QScrollArea,
        QSystemTrayIcon,
        QVBoxLayout,
        QHBoxLayout,
        QWidget,
        QPushButton,
        QFrame,
        QSizePolicy,
    )
except ImportError:
    sys.exit(
        "PyQt6 not found.  Install with:  sudo apt install python3-pyqt6"
    )

DATA_DIR = Path.home() / ".local" / "share" / "timetracker"
SESSIONS_FILE = DATA_DIR / "sessions.json"
CURRENT_FILE = DATA_DIR / "current.json"
DAEMON_SCRIPT = Path(__file__).parent / "daemon.py"

REFRESH_MS = 3000  # UI refresh interval

# App → colour map (fallback to a hash-based colour)
APP_COLORS: dict[str, str] = {
    "vscode": "#007acc",
    "vscodium": "#2b7bba",
    "cursor": "#8855ff",
    "neovim": "#57a143",
    "vim": "#019733",
    "firefox": "#ff7139",
    "chrome": "#4285f4",
    "chromium": "#4285f4",
    "terminal": "#4caf50",
    "python": "#3572a5",
    "pycharm": "#21d789",
    "intellij": "#fe315d",
    "code": "#007acc",
    "gnome-text-editor": "#f5c211",
    "gedit": "#f5c211",
}

BG = "#1a1a1a"
BG2 = "#252525"
FG = "#e0e0e0"
FG_DIM = "#888888"
ACCENT = "#4fc3f7"
LIVE_COLOR = "#ff5252"


def app_color(app_name: str) -> str:
    key = app_name.lower().replace(" ", "").replace("-", "")
    for k, v in APP_COLORS.items():
        if k in key or key in k:
            return v
    # Deterministic hash colour
    h = hash(app_name) & 0xFFFFFF
    r, g, b = (h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF
    # Desaturate a little and brighten
    avg = (r + g + b) // 3
    r = min(255, r // 2 + avg // 2 + 60)
    g = min(255, g // 2 + avg // 2 + 60)
    b = min(255, b // 2 + avg // 2 + 60)
    return f"#{r:02x}{g:02x}{b:02x}"


def make_icon(color: str, size: int = 22) -> QIcon:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.end()
    return QIcon(px)


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m = seconds // 60
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m"


def load_current() -> dict | None:
    try:
        data = json.loads(CURRENT_FILE.read_text())
        return data if data else None
    except Exception:
        return None


def load_today_sessions() -> list:
    today = date.today().isoformat()
    try:
        all_sessions = json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return []
    return [s for s in all_sessions if s.get("start", "").startswith(today)]


def daemon_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "daemon.py"], capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def start_daemon():
    subprocess.Popen(
        [sys.executable, str(DAEMON_SCRIPT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_daemon():
    subprocess.run(["pkill", "-f", "daemon.py"])


# ---------------------------------------------------------------------------
# Popup window
# ---------------------------------------------------------------------------

class SessionPopup(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(f"background: {BG}; border-radius: 10px;")
        self.setMinimumWidth(360)
        self.setMaximumWidth(480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {BG}; border-radius:10px;"
            f" border: 1px solid #333; }}"
        )
        outer.addWidget(frame)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        title_lbl = QLabel("Time Tracker")
        title_lbl.setStyleSheet(f"color:{FG}; font-weight:bold; font-size:14px;")
        header.addWidget(title_lbl)
        header.addStretch()
        self.daemon_btn = QPushButton()
        self.daemon_btn.setFixedSize(24, 24)
        self.daemon_btn.setStyleSheet(
            "QPushButton { background:#333; border-radius:4px; color:#ccc;"
            " font-size:11px; border:none; }"
            "QPushButton:hover { background:#444; }"
        )
        self.daemon_btn.clicked.connect(self._toggle_daemon)
        header.addWidget(self.daemon_btn)
        layout.addLayout(header)

        # Current session
        self.live_lbl = QLabel()
        self.live_lbl.setStyleSheet(
            f"color:{LIVE_COLOR}; font-size:11px; font-weight:bold;"
        )
        self.live_lbl.setWordWrap(True)
        layout.addWidget(self.live_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:#333;")
        layout.addWidget(sep)

        # Scroll area for sessions
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(380)
        scroll.setStyleSheet(
            "QScrollArea { border:none; background:transparent; }"
            "QScrollBar:vertical { width:6px; background:#1a1a1a; }"
            "QScrollBar::handle:vertical { background:#444; border-radius:3px; }"
        )
        self.sessions_widget = QWidget()
        self.sessions_widget.setStyleSheet(f"background:{BG};")
        self.sessions_layout = QVBoxLayout(self.sessions_widget)
        self.sessions_layout.setContentsMargins(0, 0, 0, 0)
        self.sessions_layout.setSpacing(2)
        scroll.setWidget(self.sessions_widget)
        layout.addWidget(scroll)

        self.refresh()

    def _toggle_daemon(self):
        if daemon_running():
            stop_daemon()
        else:
            start_daemon()
        QTimer.singleShot(800, self.refresh)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def refresh(self):
        self._clear_layout(self.sessions_layout)
        running = daemon_running()
        self.daemon_btn.setText("■" if running else "▶")
        self.daemon_btn.setToolTip("Stop daemon" if running else "Start daemon")

        current = load_current()
        if current and current.get("app"):
            now = datetime.now().astimezone()
            try:
                start = datetime.fromisoformat(current["start"])
                elapsed = (now - start).total_seconds()
                dur_str = fmt_duration(elapsed)
            except Exception:
                dur_str = "?"
            color = app_color(current["app"])
            self.live_lbl.setText(
                f"● LIVE  {current['app']}  ·  {current.get('context','')[:40]}  ·  {dur_str}"
            )
            self.live_lbl.setStyleSheet(
                f"color:{color}; font-size:11px; font-weight:bold;"
            )
        else:
            self.live_lbl.setText("● idle")
            self.live_lbl.setStyleSheet(f"color:{FG_DIM}; font-size:11px;")

        sessions = load_today_sessions()
        if current and current.get("app"):
            # Add a synthetic in-progress entry
            sessions = list(sessions) + [
                {
                    "app": current["app"],
                    "context": current.get("context", ""),
                    "start": current["start"],
                    "end": None,
                }
            ]

        # Group: app → context → list of sessions
        by_app: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for s in sessions:
            by_app[s["app"]][s.get("context", "")].append(s)

        if not by_app:
            lbl = QLabel("No sessions today")
            lbl.setStyleSheet(f"color:{FG_DIM}; font-size:12px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sessions_layout.addWidget(lbl)
            return

        for app_name, contexts in sorted(by_app.items()):
            color = app_color(app_name)

            # App header row
            app_total = 0.0
            for ctx_sessions in contexts.values():
                for s in ctx_sessions:
                    app_total += _session_duration(s)

            app_row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color}; font-size:14px;")
            dot.setFixedWidth(18)
            app_row.addWidget(dot)
            app_lbl = QLabel(app_name)
            app_lbl.setStyleSheet(f"color:{FG}; font-weight:bold; font-size:13px;")
            app_row.addWidget(app_lbl)
            app_row.addStretch()
            total_lbl = QLabel(fmt_duration(app_total))
            total_lbl.setStyleSheet(f"color:{color}; font-size:12px;")
            app_row.addWidget(total_lbl)
            row_w = QWidget()
            row_w.setLayout(app_row)
            self.sessions_layout.addWidget(row_w)

            for ctx, ctx_sessions in sorted(contexts.items()):
                ctx_total = sum(_session_duration(s) for s in ctx_sessions)
                is_live = any(s.get("end") is None for s in ctx_sessions)

                ctx_row = QHBoxLayout()
                ctx_row.setContentsMargins(18, 0, 0, 0)
                ctx_lbl = QLabel(ctx[:48] if ctx else "(no context)")
                ctx_lbl.setStyleSheet(
                    f"color:{'#e0e0e0' if is_live else FG_DIM}; font-size:12px;"
                )
                ctx_row.addWidget(ctx_lbl)
                if is_live:
                    live_badge = QLabel("LIVE")
                    live_badge.setStyleSheet(
                        f"color:{LIVE_COLOR}; font-size:10px; font-weight:bold;"
                        " padding: 1px 4px; border: 1px solid #ff5252; border-radius:3px;"
                    )
                    ctx_row.addWidget(live_badge)
                ctx_row.addStretch()
                dur_lbl = QLabel(fmt_duration(ctx_total))
                dur_lbl.setStyleSheet(f"color:{FG_DIM}; font-size:12px;")
                ctx_row.addWidget(dur_lbl)
                cw = QWidget()
                cw.setLayout(ctx_row)
                self.sessions_layout.addWidget(cw)

        self.sessions_layout.addStretch()
        self.adjustSize()


def _session_duration(s: dict) -> float:
    try:
        start = datetime.fromisoformat(s["start"])
        end = datetime.fromisoformat(s["end"]) if s.get("end") else datetime.now().astimezone()
        return max(0.0, (end - start).total_seconds())
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

class TrayIcon(QSystemTrayIcon):
    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._popup: SessionPopup | None = None
        self.setIcon(make_icon("#888888"))
        self.setToolTip("Time Tracker")

        menu = QMenu()
        menu.setStyleSheet(
            f"QMenu {{ background:{BG}; color:{FG}; border:1px solid #333; }}"
            "QMenu::item:selected { background: #2a2a2a; }"
        )
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(app.quit)
        self.setContextMenu(menu)

        self.activated.connect(self._on_activated)

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh_icon)
        self._timer.start(REFRESH_MS)
        self._refresh_icon()

    def _refresh_icon(self):
        current = load_current()
        if current and current.get("app"):
            color = app_color(current["app"])
            self.setIcon(make_icon(color))
            self.setToolTip(f"{current['app']} — {current.get('context','')[:50]}")
        else:
            self.setIcon(make_icon("#555555"))
            self.setToolTip("Time Tracker — idle")

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_popup()

    def _show_popup(self):
        if self._popup and self._popup.isVisible():
            self._popup.hide()
            return
        if self._popup is None:
            self._popup = SessionPopup()
        self._popup.refresh()
        # Position near tray
        geo = self._app.primaryScreen().geometry()
        popup_w = 400
        popup_h = self._popup.sizeHint().height()
        x = geo.right() - popup_w - 20
        y = geo.bottom() - popup_h - 60
        self._popup.move(x, y)
        self._popup.show()
        self._popup.raise_()


# ---------------------------------------------------------------------------
# Fallback floating window (when tray is unavailable)
# ---------------------------------------------------------------------------

class FloatingWidget(QWidget):
    """Small always-on-top widget shown when the system tray is not available."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(220, 40)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        self.dot = QLabel("●")
        self.dot.setStyleSheet("color:#888; font-size:16px;")
        layout.addWidget(self.dot)

        self.label = QLabel("Time Tracker")
        self.label.setStyleSheet(f"color:{FG}; font-size:12px; background:transparent;")
        layout.addWidget(self.label)
        layout.addStretch()

        self.setStyleSheet(
            f"QWidget {{ background:{BG}; border-radius:8px;"
            f" border: 1px solid #333; }}"
        )

        self._popup: SessionPopup | None = None
        self._dragging = False
        self._drag_pos = QPoint()

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(REFRESH_MS)
        self._refresh()
        self._reposition()

    def _reposition(self):
        screen = QApplication.primaryScreen().geometry()
        self.move(screen.right() - self.width() - 16, screen.bottom() - self.height() - 48)

    def _refresh(self):
        current = load_current()
        if current and current.get("app"):
            color = app_color(current["app"])
            self.dot.setStyleSheet(f"color:{color}; font-size:16px;")
            ctx = current.get("context", "")
            label = f"{current['app']}"
            if ctx:
                label += f" · {ctx[:20]}"
            self.label.setText(label)
        else:
            self.dot.setStyleSheet(f"color:#555; font-size:16px;")
            self.label.setText("idle")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if (event.globalPosition().toPoint() - (self._drag_pos + self.frameGeometry().topLeft())).manhattanLength() < 5:
                self._show_popup()
            self._dragging = False

    def _show_popup(self):
        if self._popup and self._popup.isVisible():
            self._popup.hide()
            return
        if self._popup is None:
            self._popup = SessionPopup()
        self._popup.refresh()
        pos = self.pos()
        self._popup.move(pos.x() - self._popup.sizeHint().width() + self.width(), pos.y() - self._popup.sizeHint().height() - 4)
        self._popup.show()
        self._popup.raise_()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background:{BG}; color:{FG}; border:1px solid #333; }}"
            "QMenu::item:selected { background:#2a2a2a; }"
        )
        menu.addAction("Quit", QApplication.instance().quit)
        menu.exec(event.globalPos())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Required env for Wayland + Qt
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # XWayland fallback
    # For native Wayland Qt (if xcb not available):
    # os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("TimeTracker")

    # Ensure daemon is running
    if not daemon_running():
        start_daemon()

    use_tray = QSystemTrayIcon.isSystemTrayAvailable()

    if use_tray:
        tray = TrayIcon(app)
        tray.show()
        # Keep a reference so it isn't garbage-collected
        app._tray = tray
    else:
        print("[tray] System tray not available — using floating widget fallback")
        widget = FloatingWidget()
        widget.show()
        app._widget = widget

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
