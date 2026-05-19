#!/usr/bin/env bash
# Time Tracker — uninstaller
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
AUTOSTART_DIR="$HOME/.config/autostart"
DATA_DIR="$HOME/.local/share/timetracker"

echo "==> Arrêt des processus en cours..."
pkill -f "daemon.py" 2>/dev/null && echo "    démon arrêté" || echo "    (démon non actif)"
pkill -f "tray.py"   2>/dev/null && echo "    tray arrêté"  || echo "    (tray non actif)"

echo ""
echo "==> Suppression des scripts..."
rm -f "$BIN_DIR/timetracker-daemon"
rm -f "$BIN_DIR/timetracker-tray"
rm -f "$BIN_DIR/timetracker-report"
rm -f "$BIN_DIR/tt"
echo "    OK"

echo ""
echo "==> Suppression de l'entrée autostart..."
rm -f "$AUTOSTART_DIR/timetracker-tray.desktop"
echo "    OK"

echo ""
read -r -p "Supprimer aussi les données (sessions, rapports) dans $DATA_DIR ? [o/N] " confirm
if [[ "$confirm" =~ ^[oO]$ ]]; then
    rm -rf "$DATA_DIR"
    echo "    Données supprimées."
else
    echo "    Données conservées dans $DATA_DIR"
fi

echo ""
echo "==========================================="
echo "  Désinstallation terminée."
echo "==========================================="
echo ""
echo "  Les paquets apt (python3-pyqt6, xdotool, etc.) n'ont pas été"
echo "  supprimés car ils peuvent être utilisés par d'autres programmes."
echo "  Pour les supprimer manuellement :"
echo "    sudo apt remove xdotool xprintidle"
echo ""
