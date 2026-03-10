#!/usr/bin/env bash
# install.sh — Setup MS/TP Tools trên Raspberry Pi 3
# Chạy: sudo bash install.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================="
echo "  MS/TP Tools — Pi Setup Script"
echo "========================================="

# ── Detect USB-RS485 adapter ────────────────────────────────────────────────
echo ""
echo "[1/5] Detect USB-RS485 adapter ..."
USB_PORTS=$(ls /dev/ttyUSB* 2>/dev/null || true)
AMA_PORTS=$(ls /dev/ttyAMA* 2>/dev/null || true)
echo "      ttyUSB: ${USB_PORTS:-none}"
echo "      ttyAMA: ${AMA_PORTS:-none}"

DEFAULT_PORT=""
if [[ -n "$USB_PORTS" ]]; then
    DEFAULT_PORT=$(echo "$USB_PORTS" | head -1)
elif [[ -n "$AMA_PORTS" ]]; then
    DEFAULT_PORT=$(echo "$AMA_PORTS" | head -1)
fi

if [[ -n "$DEFAULT_PORT" ]]; then
    echo "  → Detected: $DEFAULT_PORT"
    # Update config.yaml
    if command -v sed &>/dev/null; then
        sed -i "s|port: /dev/ttyUSB0|port: $DEFAULT_PORT|g" "$SCRIPT_DIR/config.yaml"
        echo "  → Updated config.yaml: port = $DEFAULT_PORT"
    fi
else
    echo "  ⚠️  No serial port detected. Connect adapter and check /dev/ttyUSB*"
fi

# ── Install Python dependencies ─────────────────────────────────────────────
echo ""
echo "[2/5] Install Python dependencies ..."
pip3 install -r "$SCRIPT_DIR/requirements.txt" --quiet
echo "  → Done"

# ── Serial port permissions ──────────────────────────────────────────────────
echo ""
echo "[3/5] Fix serial port permissions ..."
CURRENT_USER="${SUDO_USER:-$(whoami)}"
if ! groups "$CURRENT_USER" | grep -q dialout; then
    usermod -aG dialout "$CURRENT_USER"
    echo "  → Added $CURRENT_USER to group 'dialout'"
    echo "  ⚠️  Log out and back in for group change to take effect"
else
    echo "  → OK ($CURRENT_USER already in dialout)"
fi

# ── systemd service ──────────────────────────────────────────────────────────
echo ""
echo "[4/5] Install systemd service ..."
SERVICE_SRC="$SCRIPT_DIR/mstp-tools.service"
SERVICE_DST="/etc/systemd/system/mstp-tools.service"

# Patch ExecStart path in service file
PYTHON=$(command -v python3)
sed -e "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" \
    -e "s|__PYTHON__|$PYTHON|g" \
    "$SERVICE_SRC" > "$SERVICE_DST"

systemctl daemon-reload
systemctl enable mstp-tools.service
echo "  → Service installed and enabled"

# ── Test import ──────────────────────────────────────────────────────────────
echo ""
echo "[5/5] Quick import test ..."
cd "$SCRIPT_DIR"
$PYTHON -c "from scanner import MstpScanner; print('  → scanner OK')"
$PYTHON -c "from health_monitor import HealthMonitor; print('  → health_monitor OK')"
$PYTHON -c "from bridge import MstpBridge; print('  → bridge OK')"
$PYTHON -c "from dashboard import app; print('  → dashboard OK')"

echo ""
echo "========================================="
echo "  Setup complete!"
echo ""
echo "  Start scanner (one-shot):"
echo "    cd $SCRIPT_DIR && python3 scanner.py"
echo ""
echo "  Start dashboard:"
echo "    cd $SCRIPT_DIR && python3 dashboard.py"
echo "    → http://$(hostname -I | awk '{print $1}'):8765"
echo ""
echo "  Start as service:"
echo "    sudo systemctl start mstp-tools"
echo "    sudo journalctl -u mstp-tools -f"
echo "========================================="
