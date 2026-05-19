#!/usr/bin/env bash
# Time Tracker — installer for Ubuntu 24 GNOME/Wayland
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
AUTOSTART_DIR="$HOME/.config/autostart"

echo "==> Installing system dependencies..."
sudo apt-get install -y \
    python3-pyqt6 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-wnck-3.0 \
    xdotool \
    xprintidle \
    dbus-x11

echo ""
echo "==> Checking for GNOME extension 'Window Calls Extended' (id 4974)..."
if command -v gnome-extensions &>/dev/null; then
    if gnome-extensions list 2>/dev/null | grep -q "windowcallsextended"; then
        echo "    ✓ Extension already installed"
    else
        echo "    ✗ Not installed — window detection will fall back to xdotool/wnck"
        echo "    Install from: https://extensions.gnome.org/extension/4974/window-calls-extended/"
        echo "    (Recommended for best Wayland support)"
    fi
fi

echo ""
echo "==> Installing scripts to $BIN_DIR..."
mkdir -p "$BIN_DIR"

# Copy scripts
cp "$SCRIPT_DIR/daemon.py" "$BIN_DIR/timetracker-daemon"
cp "$SCRIPT_DIR/tray.py"   "$BIN_DIR/timetracker-tray"
cp "$SCRIPT_DIR/tt.py"     "$BIN_DIR/tt"
cp "$SCRIPT_DIR/report.py" "$BIN_DIR/timetracker-report"

chmod +x "$BIN_DIR/timetracker-daemon"
chmod +x "$BIN_DIR/timetracker-tray"
chmod +x "$BIN_DIR/tt"
chmod +x "$BIN_DIR/timetracker-report"

# Fix shebang to use system python3
sed -i '1s|.*|#!/usr/bin/env python3|' "$BIN_DIR/timetracker-daemon"
sed -i '1s|.*|#!/usr/bin/env python3|' "$BIN_DIR/timetracker-tray"
sed -i '1s|.*|#!/usr/bin/env python3|' "$BIN_DIR/tt"
sed -i '1s|.*|#!/usr/bin/env python3|' "$BIN_DIR/timetracker-report"

echo ""
echo "==> Creating autostart entry..."
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/timetracker-tray.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Time Tracker Tray
Exec=$BIN_DIR/timetracker-tray
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=Time tracker system tray
EOF

echo ""
echo "==> Creating data directories..."
mkdir -p "$HOME/.local/share/timetracker/reports"

echo ""
echo "========================================="
echo "  Installation complete!"
echo "========================================="
echo ""
echo "  Start tray:    timetracker-tray"
echo "  Start daemon:  timetracker-daemon"
echo "  CLI:           tt today / tt current / tt week / tt report --week"
echo ""
echo "  The tray will auto-start on next login."
echo ""
echo "  NOTE: If $BIN_DIR is not in your PATH, add this to ~/.bashrc:"
echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
echo ""
