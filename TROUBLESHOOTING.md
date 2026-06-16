# DashView Troubleshooting Guide

## Quick Diagnostics

```bash
# Check if DashView is running
systemctl status dashview

# Check logs
journalctl -u dashview -n 50 --no-pager

# Check health endpoint
curl -sk https://localhost/api/health | python3 -m json.tool
# or if HTTP only:
curl -s http://localhost:8080/api/health | python3 -m json.tool

# Tail live logs
tail -f /var/log/dashview.log
```

---

## Common Issues

### ❌ Dashboard won't load / spinning forever
**Cause:** JS error on page load, usually a cached old version.
**Fix:**
- iPhone: Close tab completely and reopen
- Delete home screen icon and re-add from Safari
- Hard refresh: Hold reload button in Safari → Reload Without Content Blockers

---

### ❌ DashView running HTTP instead of HTTPS
**Cause:** SSL cert path not set or cert doesn't exist.
**Fix:**
```bash
# Check cert exists
ls /etc/letsencrypt/live/YOUR_DOMAIN/

# Check env vars are set
systemctl cat dashview | grep SSL

# Set them if missing
systemctl edit dashview
# Add:
# Environment=SSL_CERT_PATH=/etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem
# Environment=SSL_KEY_PATH=/etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem
# Environment=DOMAIN=YOUR_DOMAIN

systemctl daemon-reload && systemctl restart dashview
```

---

### ❌ Push notifications not working
**Cause:** Must be opened from iPhone home screen icon, not Safari browser.
**Fix:**
1. Open URL in Safari
2. Tap Share → Add to Home Screen
3. Open from the home screen icon (not Safari)
4. Go to Settings tab → Enable Notifications → tap Allow

---

### ❌ Shadow tab shows 0 wallets / no data
**Cause:** DashView can't find the bot's shadow list.
**Fix:**
```bash
# Check shadow list exists
cat /opt/polymarket-bot/.tradingbot/shadow_traders.json

# Check BOT_DIR is set correctly
systemctl cat dashview | grep BOT_DIR

# Verify bot is running
systemctl status tradingbot-copy-bot
```

---

### ❌ Shadow tab shows fewer wallets than expected
**Cause:** Wallets added to `roster.yaml` but not to `shadow_traders.json`.
**Fix:** Wallets must be in `.tradingbot/shadow_traders.json` — that's what DashView reads.
```bash
# Check both files
cat /opt/polymarket-bot/config/roster.yaml
cat /opt/polymarket-bot/.tradingbot/shadow_traders.json
```

---

### ❌ Find a Trader returns "No profile found"
**Cause:** Wallet not in screener results and not in top 50 leaderboard.
**Fix:** Run the wallet through the screener first:
```bash
cd /opt/polymarket-bot && .venv/bin/python scripts/verify_passers.py --addresses 0xADDRESS
```
Then try Find a Trader again — it reads from screener results.

---

### ❌ Chart tab shows no data
**Cause:** No resolved trades in shadow log yet.
**Fix:** This populates automatically as shadow wallets resolve trades. Check log exists:
```bash
ls -la /opt/polymarket-bot/logs/shadow_decisions.jsonl
wc -l /opt/polymarket-bot/logs/shadow_decisions.jsonl
```

---

### ❌ Screener tab shows no Tier 1/2 passers
**Cause:** Scanner hasn't been run yet, or results are in wrong directory.
**Fix:**
```bash
# Check screener output exists
ls /tmp/screen-v3/

# Run scanner manually
cd /opt/polymarket-bot && ./scripts/run_pipeline.sh >> /tmp/screen-v3/cron.log 2>&1 &

# Or trigger from DashView Screener tab → Run Full Pipeline
```

---

### ❌ DashView crashes on startup
**Cause:** Usually a Python import error — BOT_DIR wrong or missing dependencies.
**Fix:**
```bash
# Check journalctl for the actual error
journalctl -u dashview -n 30 --no-pager

# Test manually
cd /opt/dashview && /opt/polymarket-bot/.venv/bin/python server.py

# Check BOT_DIR has the src/ package
ls /opt/polymarket-bot/src/
```

---

### ❌ "Failed to connect" when testing API
**Cause:** DashView is on port 8080 (HTTP) not 443 (HTTPS).
**Fix:**
```bash
# Test HTTP
curl http://localhost:8080/api/health

# Test HTTPS
curl -k https://localhost/api/health
```

---

### ❌ DuckDNS domain not resolving
**Cause:** DuckDNS cron not running or IP changed.
**Fix:**
```bash
# Run DuckDNS update manually
bash /opt/duckdns/duck.sh

# Check cron is set
crontab -l | grep duck
```

---

### ❌ SSL cert expired
**Cause:** Let's Encrypt certs expire every 90 days.
**Fix:**
```bash
certbot renew
systemctl restart dashview
```

---

## Environment Variables Reference

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

## Getting Help

If you're stuck, paste this into Claude:
> "I'm having trouble with DashView. Here's my error: [paste error]. Here's my health check: [paste curl output]. Help me fix it."
