#!/bin/bash
# DashView start script
# Set BOT_NAME and BOT_DIR before running, or edit below

BOT_DIR="${BOT_DIR:-/opt/polymarket-bot}"
BOT_NAME="${BOT_NAME:-MyBot}"
DASHVIEW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8080}"

echo "==================================="
echo "  DashView Setup"
echo "==================================="
echo ""

if [ ! -d "$BOT_DIR" ]; then
  echo "ERROR: Bot directory not found at $BOT_DIR"
  echo "Set BOT_DIR env var to your bot installation path"
  echo "Example: BOT_DIR=/opt/my-bot ./start.sh"
  exit 1
fi

echo "[1/3] Installing Flask..."
"$BOT_DIR/.venv/bin/pip" install flask --quiet

echo "[2/3] Opening firewall port $PORT..."
ufw allow "$PORT/tcp" 2>/dev/null || true

echo "[3/3] Starting DashView on port $PORT..."
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "YOUR_VPS_IP")

echo ""
echo "==================================="
echo "  DashView is running!"
echo ""
echo "  Open Safari on iPhone and go to:"
echo "  http://$PUBLIC_IP:$PORT"
echo ""
echo "  Then tap Share → Add to Home Screen"
echo "==================================="
echo ""

BOT_DIR="$BOD_DIR" BOT_NAME="$BOT_NAME" "$BOT_DIR/.venv/bin/python" "$DASHVIEW_DIR/server.py" --port "$PORT"
