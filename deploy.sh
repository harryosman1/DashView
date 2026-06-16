#!/usr/bin/env bash
# DashView deploy script — sets up DashView on a fresh VPS
# Usage: bash deploy.sh
set -euo pipefail

echo ""
echo "╔══════════════════════════════════════╗"
echo "║       DashView Setup Wizard          ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Collect config ──────────────────────────────────────────────────────────
read -p "Bot directory (e.g. /opt/polymarket-bot): " BOT_DIR
BOT_DIR=${BOT_DIR:-/opt/polymarket-bot}

read -p "DashView install directory (default: /opt/dashview): " DASHVIEW_HOME
DASHVIEW_HOME=${DASHVIEW_HOME:-/opt/dashview}

read -p "Bot name (default: Atlas): " BOT_NAME
BOT_NAME=${BOT_NAME:-Atlas}

read -p "Starting capital in USD (default: 500): " CAPITAL
CAPITAL=${CAPITAL:-500}

read -p "Risk % per trade (default: 1.5): " RISK_PCT
RISK_PCT=${RISK_PCT:-1.5}

read -p "Your DuckDNS domain (e.g. mybot.duckdns.org): " DOMAIN
DOMAIN=${DOMAIN:-""}

read -p "Go-live decision date as Unix timestamp (default: 1751155200 = Jun 27 2026): " DECISION_DATE
DECISION_DATE=${DECISION_DATE:-1751155200}

# ── Derive SSL paths ─────────────────────────────────────────────────────────
if [ -n "$DOMAIN" ]; then
    SSL_CERT_PATH="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
    SSL_KEY_PATH="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
else
    SSL_CERT_PATH=""
    SSL_KEY_PATH=""
fi

# ── Install dependencies ─────────────────────────────────────────────────────
echo ""
echo "▶ Installing Python dependencies..."
PYTHON="${BOT_DIR}/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    echo "⚠️  No venv found at ${BOT_DIR}/.venv — using system python3"
    PYTHON=$(which python3)
fi
$PYTHON -m pip install flask httpx pyyaml --quiet

# ── Write systemd service ────────────────────────────────────────────────────
echo "▶ Writing systemd service..."
cat > /etc/systemd/system/dashview.service << SVCEOF
[Unit]
Description=DashView Mobile Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${DASHVIEW_HOME}
ExecStart=${PYTHON} ${DASHVIEW_HOME}/server.py --port 443
Restart=always
RestartSec=5
Environment=BOT_DIR=${BOT_DIR}
Environment=BOT_NAME=${BOT_NAME}
Environment=DASHVIEW_HOME=${DASHVIEW_HOME}
Environment=CAPITAL=${CAPITAL}
Environment=RISK_PCT=${RISK_PCT}
Environment=DECISION_DATE=${DECISION_DATE}
Environment=DOMAIN=${DOMAIN}
Environment=SSL_CERT_PATH=${SSL_CERT_PATH}
Environment=SSL_KEY_PATH=${SSL_KEY_PATH}
Environment=PM_SCREEN_DIR=/tmp/screen-v3

[Install]
WantedBy=multi-user.target
SVCEOF

# ── Enable and start ─────────────────────────────────────────────────────────
echo "▶ Enabling and starting DashView..."
systemctl daemon-reload
systemctl enable dashview
systemctl restart dashview
sleep 3

# ── Health check ─────────────────────────────────────────────────────────────
echo ""
echo "▶ Running health check..."
if curl -sk https://localhost/api/health | grep -q '"ok": true'; then
    echo "✅ DashView is running on HTTPS!"
elif curl -s http://localhost:8080/api/health | grep -q '"ok": true'; then
    echo "✅ DashView is running on HTTP (port 8080)"
    echo "⚠️  HTTPS not active — check your SSL cert path"
else
    echo "❌ DashView failed to start — check: journalctl -u dashview -n 30"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  DashView is live!                                       ║"
if [ -n "$DOMAIN" ]; then
echo "║  Open: https://${DOMAIN}"
fi
echo "║                                                          ║"
echo "║  iPhone setup:                                           ║"
echo "║  1. Open the URL in Safari                               ║"
echo "║  2. Tap Share → Add to Home Screen                       ║"
echo "║  3. Open from home screen icon                           ║"
echo "║  4. Go to Settings → Enable Notifications                ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
