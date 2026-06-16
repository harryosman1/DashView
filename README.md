# DashView

Mobile dashboard for a Polymarket copy-trading bot. Features: shadow & live P&L tracking, position detail, wallet screener with Tier 1/2 analysis, Find a Trader lookup, watchlist, sizing calculator, P&L chart, push notifications, dark/light mode.

## Features

- **Shadow Tab** — P&L per wallet, tap for position detail (Copied/Shadowing/Closed)
- **Live Tab** — Real P&L once you go live
- **Sizing Tab** — What-if calculator + countdown to capital decision date
- **Chart Tab** — Cumulative P&L chart, daily breakdown, realistic simulation engine
- **Screener Tab** — Find a Trader, Tier 1/2 passers, watchlist import/export, scan history, promote buttons
- **Settings Tab** — Push notifications (6 toggles), dark/light mode, bot config

---

## Requirements

- A running Polymarket copy-trading bot (with `.tradingbot/shadow_traders.json`)
- Python 3.10+
- A VPS with a public IP
- DuckDNS domain + Let's Encrypt SSL (required for iPhone push notifications)

---

## Quick Install

```bash
git clone https://github.com/harryosman1/DashView.git /opt/dashview
cd /opt/dashview
bash deploy.sh
```

The deploy script will:
1. Ask for your bot directory, domain, capital, and risk settings
2. Install Python dependencies
3. Write and enable the systemd service with all env vars
4. Start DashView and run a health check

---

## Manual Install

### 1. Clone the repo
```bash
git clone https://github.com/harryosman1/DashView.git /opt/dashview
cd /opt/dashview
```

### 2. Install dependencies
```bash
/opt/your-bot/.venv/bin/pip install flask httpx pyyaml
```

### 3. Set up systemd service
```bash
cat > /etc/systemd/system/dashview.service << 'SVCEOF'
[Unit]
Description=DashView Mobile Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/dashview
ExecStart=/opt/your-bot/.venv/bin/python /opt/dashview/server.py --port 443
Restart=always
RestartSec=5
Environment=BOT_DIR=/opt/your-bot
Environment=BOT_NAME=YourBotName
Environment=DASHVIEW_HOME=/opt/dashview
Environment=CAPITAL=500
Environment=RISK_PCT=1.5
Environment=DOMAIN=yourname.duckdns.org
Environment=SSL_CERT_PATH=/etc/letsencrypt/live/yourname.duckdns.org/fullchain.pem
Environment=SSL_KEY_PATH=/etc/letsencrypt/live/yourname.duckdns.org/privkey.pem
Environment=PM_SCREEN_DIR=/tmp/screen-v3
SVCEOF

systemctl daemon-reload
systemctl enable dashview
systemctl start dashview
```

### 4. Set up DuckDNS + SSL
```bash
# Install certbot
apt install certbot -y

# Get SSL cert (stop any service on port 80 first)
certbot certonly --standalone -d yourname.duckdns.org

# Set up DuckDNS auto-update
mkdir -p /opt/duckdns
cat > /opt/duckdns/duck.sh << 'DNSEOF'
echo url="https://www.duckdns.org/update?domains=YOURNAME&token=YOUR_TOKEN&ip=" | curl -k -o /opt/duckdns/duck.log -K -
DNSEOF
chmod +x /opt/duckdns/duck.sh

# Add to cron (runs every 5 minutes)
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/duckdns/duck.sh") | crontab -
```

### 5. Add to iPhone home screen
1. Open `https://yourname.duckdns.org` in Safari
2. Tap **Share → Add to Home Screen**
3. Open DashView from the home screen icon
4. Go to **Settings → Enable Notifications** → tap Allow

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BOT_DIR` | `/opt/polymarket-bot` | Path to your trading bot |
| `DASHVIEW_HOME` | `/opt/dashview` | Path to DashView install |
| `BOT_NAME` | `Atlas` | Your bot's display name |
| `CAPITAL` | `500` | Starting capital in USD |
| `RISK_PCT` | `1.5` | Risk % per trade |
| `DECISION_DATE` | `1751155200` | Go-live date as Unix timestamp |
| `DOMAIN` | — | Your DuckDNS domain |
| `SSL_CERT_PATH` | — | Path to fullchain.pem |
| `SSL_KEY_PATH` | — | Path to privkey.pem |
| `PM_SCREEN_DIR` | `/tmp/screen-v3` | Screener output directory |
| `DASHVIEW_LOG` | `/var/log/dashview.log` | Log file path |

---

## Screener Integration

DashView reads screener results from `PM_SCREEN_DIR` (`/tmp/screen-v3` by default).
Screener scripts should live in `BOT_DIR/scripts/`.

```bash
# Verify a specific wallet
cd /opt/your-bot && .venv/bin/python scripts/verify_passers.py --addresses 0xADDRESS

# Run full pipeline
cd /opt/your-bot && ./scripts/run_pipeline.sh

# Or trigger from DashView Screener tab → Run Full Pipeline
```

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check with dependency status |
| `/api/data` | GET | All dashboard data |
| `/api/trader/<addr>` | GET | Look up any Polymarket trader |
| `/api/positions/<trader>` | GET | Open/closed positions for a wallet |
| `/api/watchlist` | GET/POST | Manage watchlist |
| `/api/watchlist/pnl` | GET | Fetch live P&L for watchlist wallets |
| `/api/watchlist/export` | GET | Export watchlist as CSV or JSON |
| `/api/watchlist/import` | POST | Bulk import wallets |
| `/api/promote` | POST | Add wallet to shadow or live roster |
| `/api/run-scanner` | POST | Trigger full screener pipeline |
| `/api/run-network` | POST | Trigger network follow scan |
| `/api/chart` | GET | P&L chart data |
| `/api/simulation` | GET | Realistic capital simulation |
| `/api/config` | GET/POST | Bot configuration |

---

## ⚠️ Critical Notes

1. **NEVER copy `server.py` from GitHub to your VPS** — GitHub has placeholder paths. Edit env vars instead.
2. The **Live button** only updates `roster.yaml` — it does NOT flip paper→live mode.
3. Push notifications require HTTPS + opening from iPhone home screen icon.
4. Shadow wallet list is read from `.tradingbot/shadow_traders.json` — not `roster.yaml`.

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues and fixes.

If you're stuck, paste this into Claude:
> "I'm having trouble with DashView. Here's my error: [paste error]. Here's my health check output: [paste curl output]. Help me fix it."

---

## Changelog

### v2.1 (Jun 16 2026)
- Added **Find a Trader** — look up any wallet by address or Polymarket URL
- Added **Watchlist import/export** — CSV and JSON support
- Added **Config class** — all settings via env vars, no code editing required
- Added **DASHVIEW_HOME** env var — install anywhere
- Added **SSL env vars** — set your domain without touching code
- Added **Real health check** — verifies bot dir, logs, SSL cert, bot running
- Added **Structured logging** — to stdout + `/var/log/dashview.log`
- Added **deploy.sh** — one-script install wizard
- Fixed duplicate DOM ids in Screener tab
- Fixed XSS via labels (escapeHtml/escapeAttr)
- Improved address validation (strict regex)
- Clipboard fallback for non-HTTPS environments

### v2.0
- Full mobile dashboard with 6 tabs
- Shadow roster P&L tracking
- Realistic simulation engine
- Push notifications
- DuckDNS + SSL support
