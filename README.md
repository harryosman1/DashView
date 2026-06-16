# DashView

Mobile dashboard for Polymarket shadow trading, built on top of the Atlas copy-trading bot.

## Features

- **Shadow Tab** — P&L per wallet, tap for position detail (Copied/Shadowing/Closed)
- **Live Tab** — Real P&L (auto-populates after go-live)
- **Sizing Tab** — What-if calculator with capital input + countdown to decision date
- **Chart Tab** — Shadow + live P&L chart, sized view, W/L stats, realistic simulation
- **Screener Tab** — Find a Trader lookup, Tier 1/2 results, watchlist, scan history, promote buttons
- **Settings Tab** — Push notifications (6 toggles), dark/light mode, bot config

## Requirements

- Atlas/tradingbot bot installed at `/opt/polymarket-bot` (or custom path)
- Python 3.10+
- Flask + httpx + pyyaml
- DuckDNS domain + Let's Encrypt SSL (for HTTPS + iPhone push notifications)

## Setup

```bash
git clone https://github.com/harryosman1/DashView.git /opt/dashview
cd /opt/dashview
cp .env.example .env
# Edit .env with your values
```

Configure your domain in `server.py` — replace `YOUR_DOMAIN.duckdns.org` with your actual DuckDNS domain.

## ⚠️ Critical Notes

1. **NEVER copy `server.py` from GitHub to your VPS** — GitHub has `YOUR_DOMAIN.duckdns.org` placeholder which breaks SSL. Edit in place on the VPS.
2. The **Live button** in DashView only updates `roster.yaml` — it does NOT flip paper→live mode. Contact your bot creator to go live.
3. Push notifications require HTTPS + opening from iPhone home screen icon.

## Systemd Service

```bash
cp systemd/dashview.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable dashview
systemctl start dashview
```

## Screener Integration

Screener scripts live in `/opt/polymarket-bot/scripts/`. Results are read from `/tmp/screen-v3/`.

```bash
# Verify a specific wallet
cd /opt/polymarket-bot && .venv/bin/python scripts/verify_passers.py --addresses 0xADDRESS

# Run full pipeline
cd /opt/polymarket-bot && ./scripts/run_pipeline.sh
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/data` | GET | All dashboard data |
| `/api/trader/<addr>` | GET | Look up any Polymarket trader |
| `/api/positions/<trader>` | GET | Open/closed positions for a shadow wallet |
| `/api/watchlist` | GET/POST | Manage watchlist |
| `/api/watchlist/pnl` | GET | Fetch P&L for watchlist wallets |
| `/api/watchlist/export` | GET | Export watchlist as CSV or JSON |
| `/api/watchlist/import` | POST | Bulk import wallets |
| `/api/promote` | POST | Add wallet to shadow or live roster |
| `/api/run-scanner` | POST | Trigger full screener pipeline |
| `/api/chart` | GET | P&L chart data |
| `/api/simulation` | GET | Realistic capital simulation |

## Changelog

### v2.1 (Jun 16 2026)
- Added **Find a Trader** — look up any wallet by address or Polymarket URL
- Added **Watchlist import/export** — CSV and JSON support
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
