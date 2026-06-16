#!/usr/bin/env python3
"""DashView — Mobile dashboard server for Polymarket shadow trading."""
from __future__ import annotations
import argparse, json, os, sys, time, logging
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────────────────
log_file = os.environ.get("DASHVIEW_LOG", "/var/log/dashview.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        *([] if not os.access(str(Path(log_file).parent), os.W_OK)
          else [logging.FileHandler(log_file)]),
    ],
)
logger = logging.getLogger("dashview")

# ── Config ─────────────────────────────────────────────────────────────────
class Config:
    # Paths
    BOT_DIR       = Path(os.environ.get("BOT_DIR", "/opt/polymarket-bot"))
    DASHVIEW_HOME = Path(os.environ.get("DASHVIEW_HOME", "/opt/dashview"))
    SCREEN_DIR    = Path(os.environ.get("PM_SCREEN_DIR", "/tmp/screen-v3"))

    # Trading
    CAPITAL       = float(os.environ.get("CAPITAL", 500))
    RISK_PCT      = float(os.environ.get("RISK_PCT", 1.5)) / 100
    DECISION_DATE = int(os.environ.get("DECISION_DATE", 1751155200))

    # SSL
    SSL_CERT      = os.environ.get("SSL_CERT_PATH",
                    "/etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem")
    SSL_KEY       = os.environ.get("SSL_KEY_PATH",
                    "/etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem")

    # Bot
    BOT_NAME      = os.environ.get("BOT_NAME", "Atlas")

cfg = Config()

sys.path.insert(0, str(cfg.BOT_DIR))

logger.info(f"DashView starting — BOT_DIR={cfg.BOT_DIR}, "
            f"DASHVIEW_HOME={cfg.DASHVIEW_HOME}, "
            f"capital=${cfg.CAPITAL}, risk={cfg.RISK_PCT*100}%")

from flask import Flask, jsonify, send_from_directory
app = Flask(__name__, static_folder=str(cfg.DASHVIEW_HOME / "static"))

# Legacy aliases (keeps rest of file working without changes)
BOT_DIR       = cfg.BOT_DIR
RISK_PCT      = cfg.RISK_PCT
CAPITAL       = cfg.CAPITAL
DECISION_DATE = cfg.DECISION_DATE

# ── Request timeout (circuit breaker) ──────────────────────────────────────
# Timeouts enforced at the API call level (httpx timeout=8s per call)
from functools import wraps

def with_timeout(seconds=20):
    """No-op decorator — timeouts handled at httpx call level."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper
    return decorator

def get_shadow_pnl():
    try:
        from src.pnl_cache import get_cached_shadow_pnl
        from src.shadow_list import ShadowList
        shadow = get_cached_shadow_pnl()
        all_names = [e.name for e in ShadowList().list_all()]
        result = []
        for name in all_names:
            s = shadow.get(name)
            if s:
                result.append({"name": name, "combined": round(s.combined_hypothetical_pnl,2), "resolved": round(s.resolution_realized_pnl,2), "unrealized": round(s.hypothetical_unrealized_pnl,2), "open_positions": s.open_positions_count, "active": True})
            else:
                result.append({"name": name, "combined": 0, "resolved": 0, "unrealized": 0, "open_positions": 0, "active": False})
        return result
    except Exception as e:
        logger.error(f"get_shadow_pnl failed: {e}")
        return [{"error": str(e)}]

def get_live_pnl():
    try:
        from src.pnl_cache import get_cached_pnl
        pnl = get_cached_pnl()
        return {
            "realized": round(pnl.realized_pnl, 2),
            "unrealized": round(pnl.unrealized_pnl, 2),
            "combined": round(pnl.combined_pnl, 2),
            "starting_capital": pnl.starting_capital,
            "total_value": round(pnl.total_value, 2),
            "return_pct": round(pnl.return_pct, 2),
            "realized_wins": pnl.realized_wins,
            "realized_losses": pnl.realized_losses,
            "realized_win_rate": round(pnl.realized_win_rate, 3) if pnl.realized_win_rate else 0,
            "today_pnl": round(pnl.today_pnl, 2),
            "rolling_7d_pnl": round(pnl.rolling_7d_pnl, 2),
            "open_positions": pnl.unrealized_count,
            "current_drawdown_pct": round(pnl.current_drawdown_pct, 2),
            "paper_mode": pnl.realized_pnl == 0 and pnl.realized_wins == 0,
        }
    except Exception as e:
        logger.error(f"get_live_pnl failed: {e}")
        return {"error": str(e)}

def get_sizing_estimate(shadow_wallets):
    try:
        bet_size = round(CAPITAL * RISK_PCT, 2)
        days_left = max(0, int((DECISION_DATE - time.time()) / 86400))

        # Calculate scaling ratio from shadow (base_usd=100) to real sizing
        base_usd = 100.0
        scale = bet_size / base_usd

        total_combined = sum(w.get("combined", 0) for w in shadow_wallets if w.get("active"))
        total_resolved = sum(w.get("resolved", 0) for w in shadow_wallets if w.get("active"))
        total_unrealized = sum(w.get("unrealized", 0) for w in shadow_wallets if w.get("active"))

        wallets = []
        for w in shadow_wallets:
            if w.get("active"):
                wallets.append({
                    "name": w["name"],
                    "combined": round(w["combined"] * scale, 2),
                    "resolved": round(w["resolved"] * scale, 2),
                    "unrealized": round(w["unrealized"] * scale, 2),
                    "open_positions": w["open_positions"],
                })

        return {
            "capital": CAPITAL,
            "bet_size": bet_size,
            "risk_pct": RISK_PCT * 100,
            "scale": round(scale, 4),
            "days_to_decision": days_left,
            "decision_date": "June 27, 2026",
            "estimated_combined": round(total_combined * scale, 2),
            "estimated_resolved": round(total_resolved * scale, 2),
            "estimated_unrealized": round(total_unrealized * scale, 2),
            "wallets": wallets,
        }
    except Exception as e:
        logger.error(f"get_screener_results failed: {e}")
        return {"error": str(e)}

def get_bot_status():
    try:
        import subprocess
        result = subprocess.run(["systemctl", "is-active", "tradingbot-copy-bot"], capture_output=True, text=True)
        status = result.stdout.strip()
        log_path = BOT_DIR / "logs" / "stdout.log"
        last_line = ""
        if log_path.exists():
            lines = log_path.read_text().splitlines()
            for line in reversed(lines):
                if line.strip():
                    last_line = line.strip()
                    break
        return {"running": status == "active", "status": status, "last_log": last_line, "bot_name": os.environ.get("BOT_NAME", "Atlas")}
    except Exception as e:
        logger.error(f"get_bot_status failed: {e}")
        return {"running": False, "status": "unknown", "error": str(e)}

def get_screener_results():
    try:
        screen_dir = cfg.SCREEN_DIR
        verified = {}
        vpath = screen_dir / "verified_passers.json"
        if vpath.exists():
            data = json.loads(vpath.read_text()) if vpath.read_text().strip() else []
            if isinstance(data, list):
                tier2 = [w for w in data if w.get("tier1_pass") and isinstance(w.get("tier2"), dict) and w["tier2"].get("passes")]
                tier1_only = [w for w in data if w.get("tier1_pass") and not (isinstance(w.get("tier2"), dict) and w.get("tier2",{}).get("passes"))]
                verified = {
                    "count": len(data),
                    "tier2_count": len(tier2),
                    "tier1_count": len(tier1_only),
                    "tier2": tier2[:10],
                    "tier1_only": tier1_only[:10],
                    "wallets": data[:5],
                    "last_updated": int(vpath.stat().st_mtime)
                }
        network = {}
        npath = screen_dir / "network_passers.json"
        if npath.exists():
            data = json.loads(npath.read_text())
            network = {"count": len(data) if isinstance(data, list) else 0, "wallets": data[:5] if isinstance(data, list) else [], "last_updated": int(npath.stat().st_mtime)}
        cron_log = ""
        cpath = screen_dir / "cron.log"
        if cpath.exists():
            lines = cpath.read_text().splitlines()
            cron_log = lines[-1] if lines else ""
        return {"verified": verified, "network": network, "cron_last_line": cron_log}
    except Exception as e:
        return {"error": str(e)}

@app.route("/api/data")
@with_timeout(25)
def api_data():
    shadow = get_shadow_pnl()
    return jsonify({
        "timestamp": int(time.time()),
        "shadow_pnl": shadow,
        "live_pnl": get_live_pnl(),
        "sizing": get_sizing_estimate(shadow),
        "bot_status": get_bot_status(),
        "screener": get_screener_results(),
    })

@app.route("/api/positions/<trader>")
def api_positions(trader):
    try:
        import json
        log_path = BOT_DIR / "logs" / "shadow_decisions.jsonl"
        if not log_path.exists():
            return jsonify({"positions": [], "error": "No log file"})

        opened = {}
        resolved = {}

        with open(log_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception as e:
                    continue
                if d.get("trader") != trader:
                    continue
                cid = d.get("condition_id", "")
                decision = d.get("decision", "")
                if not cid:
                    continue
                if decision == "shadow_resolution":
                    resolved[cid] = {
                        "resolution_pnl": d.get("pnl", d.get("our_pnl", None)),
                        "question": d.get("question", ""),
                        "outcome": d.get("outcome", ""),
                        "slug": d.get("slug", ""),
                        "their_price": d.get("their_price", 0),
                        "timestamp": d.get("timestamp", 0),
                    }
                elif cid not in opened:
                    opened[cid] = {
                        "condition_id": cid,
                        "question": d.get("question", ""),
                        "outcome": d.get("outcome", ""),
                        "slug": d.get("slug", ""),
                        "their_price": d.get("their_price", 0),
                        "their_usdc_size": d.get("their_usdc_size", 0),
                        "our_would_be_size": d.get("our_would_be_size", 0),
                        "current_mid": d.get("current_mid_at_decision", 0),
                        "timestamp": d.get("timestamp", 0),
                        "decision": decision,
                    }

        open_pos = {cid: d for cid, d in opened.items() if cid not in resolved}
        # Merge opened data into resolved so question/outcome/slug are always present
        closed_pos = {}
        for cid, v in resolved.items():
            base = opened.get(cid, {})
            merged = {**base, **v}
            # resolution entries overwrite pnl but we keep question/outcome/slug from opened
            if not merged.get("question") and base.get("question"):
                merged["question"] = base["question"]
            if not merged.get("outcome") and base.get("outcome"):
                merged["outcome"] = base["outcome"]
            if not merged.get("slug") and base.get("slug"):
                merged["slug"] = base["slug"]
            closed_pos[cid] = merged

        copied = sorted([p for p in open_pos.values() if p["decision"] == "copy"], key=lambda x: x["timestamp"], reverse=True)
        shadowing = sorted([p for p in open_pos.values() if p["decision"] != "copy"], key=lambda x: x["timestamp"], reverse=True)
        closed_all = sorted(closed_pos.values(), key=lambda x: x["timestamp"], reverse=True)
        closed_copied = [p for p in closed_all if p.get("decision") == "copy"]
        closed_shadow = [p for p in closed_all if p.get("decision") != "copy"]

        return jsonify({
            "trader": trader,
            "copied": copied[:50],
            "shadowing": shadowing[:100],
            "closed_copied": closed_copied[:30],
            "closed_shadow": closed_shadow[:30],
            "copied_count": len(copied),
            "shadowing_count": len(shadowing),
            "closed_copied_count": len(closed_copied),
            "closed_shadow_count": len(closed_shadow),
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/run-scanner", methods=["POST"])
def api_run_scanner():
    try:
        import subprocess
        subprocess.Popen(
            ["bash", "-c", f"cd {BOT_DIR} && rm -f /tmp/screen-v3/discovered.json && ./scripts/run_pipeline.sh >> /tmp/screen-v3/cron.log 2>&1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Promote failed: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/run-network", methods=["POST"])
def api_run_network():
    try:
        import subprocess
        subprocess.Popen(
            ["bash", "-c", f"cd {BOT_DIR} && .venv/bin/python scripts/network_follow.py >> /tmp/screen-v3/cron.log 2>&1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/screener-history")
def api_screener_history():
    try:
        import json
        screen_dir = cfg.SCREEN_DIR
        history_path = screen_dir / "scan_history.json"

        # Load existing history
        history = []
        if history_path.exists():
            try:
                history = json.loads(history_path.read_text())
            except Exception as e:
                history = []

        # Add current scan as latest entry if metrics.json exists
        metrics_path = screen_dir / "metrics.json"
        if metrics_path.exists():
            try:
                m = json.loads(metrics_path.read_text())
                ts = int(metrics_path.stat().st_mtime)
                # Only add if not already in history
                if not history or history[-1].get("timestamp") != ts:
                    verified_path = screen_dir / "verified_passers.json"
                    verified = []
                    if verified_path.exists():
                        verified = json.loads(verified_path.read_text()) or []
                    network_path = screen_dir / "network_passers.json"
                    network = []
                    if network_path.exists():
                        network = json.loads(network_path.read_text()) or []

                    entry = {
                        "timestamp": ts,
                        "date": __import__("datetime").datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                        "wallets_discovered": m.get("discovery", {}).get("wallets_human_sized", 0),
                        "screened": m.get("deep_screen", {}).get("candidates_screened", 0),
                        "passers": m.get("deep_screen", {}).get("passers", 0),
                        "verified_count": len(verified),
                        "network_count": len(network),
                        "verified": [{"address": w.get("address",""), "win_rate": w.get("win_rate",0), "pnl": w.get("pnl",0), "closed": w.get("closed",0)} for w in verified[:10]],
                        "network": [{"address": w.get("address",""), "win_rate": w.get("win_rate",0), "pnl": w.get("pnl",0), "shared": w.get("shared_markets",0)} for w in network[:10]],
                    }
                    history.append(entry)
                    # Keep last 30 scans
                    history = history[-30:]
                    history_path.write_text(json.dumps(history, indent=1))
            except Exception as e:
                pass

        return jsonify({"history": list(reversed(history))})
    except Exception as e:
        return jsonify({"history": [], "error": str(e)})

@app.route("/api/promote", methods=["POST"])
def api_promote():
    try:
        from flask import request
        import json, yaml
        data = request.get_json()
        address = data.get("address", "").lower()
        name = data.get("name", address[:8])
        mode = data.get("mode", "shadow")  # "shadow" or "live"

        if not address:
            return jsonify({"ok": False, "error": "No address provided"})

        roster_path = BOT_DIR / "config" / "roster.yaml"
        shadow_list_path = BOT_DIR / "config" / "shadow_list.yaml"

        if mode == "shadow":
            # Add to shadow_list.yaml
            shadow = []
            if shadow_list_path.exists():
                shadow = yaml.safe_load(shadow_list_path.read_text()) or []
            # Check not already there
            for w in shadow:
                if w.get("address","").lower() == address:
                    return jsonify({"ok": False, "error": "Already in shadow list"})
            shadow.append({"name": name, "address": address, "tier": "shadow"})
            shadow_list_path.write_text(yaml.dump(shadow, default_flow_style=False))
            return jsonify({"ok": True, "message": f"Added {name} to shadow list"})

        elif mode == "live":
            # Add to roster.yaml
            roster = {}
            if roster_path.exists():
                roster = yaml.safe_load(roster_path.read_text()) or {}
            traders = roster.get("traders", [])
            for w in traders:
                if w.get("address","").lower() == address:
                    return jsonify({"ok": False, "error": "Already in roster"})
            traders.append({"name": name, "address": address, "tier": "live"})
            roster["traders"] = traders
            roster_path.write_text(yaml.dump(roster, default_flow_style=False))
            return jsonify({"ok": True, "message": f"Added {name} to live roster"})

        return jsonify({"ok": False, "error": "Invalid mode"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/health")
def api_health():
    import subprocess
    checks = {
        "server": True,
        "bot_dir": cfg.BOT_DIR.exists(),
        "screen_dir": cfg.SCREEN_DIR.exists(),
        "log_readable": (cfg.BOT_DIR / "logs" / "shadow_decisions.jsonl").exists(),
        "ssl_cert": Path(cfg.SSL_CERT).exists(),
        "dashview_home": cfg.DASHVIEW_HOME.exists(),
    }
    try:
        result = subprocess.run(["systemctl", "is-active", "tradingbot-copy-bot"],
                                capture_output=True, text=True)
        checks["bot_running"] = result.stdout.strip() == "active"
    except Exception:
        checks["bot_running"] = False
    all_ok = all(checks.values())
    if not all_ok:
        logger.warning(f"Health check failed: {[k for k,v in checks.items() if not v]}")
    return jsonify({"ok": all_ok, "checks": checks}), 200 if all_ok else 503

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)

@app.route("/api/chart")
def api_chart():
    """Return daily cumulative P&L for charting."""
    try:
        import json
        from datetime import datetime
        log_path = BOT_DIR / "logs" / "shadow_decisions.jsonl"
        if not log_path.exists():
            return jsonify({"points": []})
        shadow_daily = {}
        with open(log_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception as e:
                    continue
                if d.get("decision") != "shadow_resolution":
                    continue
                ts = int(d.get("timestamp", 0))
                pnl = d.get("realized_pnl_override", d.get("pnl", d.get("our_pnl", 0))) or 0
                day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                shadow_daily[day] = shadow_daily.get(day, 0) + pnl

        # Live P&L by day (from pnl cache — single value for now)
        live_daily = {}
        try:
            from src.pnl_cache import get_cached_pnl
            pnl_cache = get_cached_pnl()
            if pnl_cache.realized_pnl != 0:
                today = datetime.now().strftime("%Y-%m-%d")
                live_daily[today] = pnl_cache.realized_pnl
        except Exception as e:
            pass

        all_days = sorted(set(list(shadow_daily.keys()) + list(live_daily.keys())))
        # Sizing: $500 portfolio, 1.5% risk = $7.50/bet, base_usd=100, scale=7.5%
        scale = 7.50 / 100.0

        points = []
        shadow_cum = 0
        live_cum = 0
        for day in all_days:
            sd = shadow_daily.get(day, 0)
            ld = live_daily.get(day, 0)
            shadow_cum += sd
            live_cum += ld
            points.append({
                "date": day,
                "shadow_daily": round(sd, 2),
                "shadow_cumulative": round(shadow_cum, 2),
                "live_daily": round(ld, 2),
                "live_cumulative": round(live_cum, 2),
                "sized_daily": round(sd * scale, 2),
                "sized_cumulative": round(shadow_cum * scale, 2),
            })
        # Copied-only W/L stats
        copied_cids = set()
        copied_wl = {"wins": 0, "losses": 0, "pnl": 0}
        with open(log_path) as f2:
            for line in f2:
                try:
                    d = json.loads(line)
                except Exception as e:
                    continue
                cid = d.get("condition_id", "")
                decision = d.get("decision", "")
                if decision == "copy":
                    copied_cids.add(cid)
                elif decision == "shadow_resolution" and cid in copied_cids:
                    pnl = d.get("realized_pnl_override", d.get("pnl", 0)) or 0
                    if pnl > 0:
                        copied_wl["wins"] += 1
                    elif pnl < 0:
                        copied_wl["losses"] += 1
                    copied_wl["pnl"] += pnl
        total = copied_wl["wins"] + copied_wl["losses"]
        copied_wl["total"] = total
        copied_wl["win_rate"] = round(copied_wl["wins"] / total * 100, 1) if total else 0
        copied_wl["sized_pnl"] = round(copied_wl["pnl"] * scale, 2)
        copied_wl["pnl"] = round(copied_wl["pnl"], 2)
        return jsonify({"points": points, "scale": scale, "bet_size": 7.50, "capital": 500, "copied_wl": copied_wl})
    except Exception as e:
        return jsonify({"points": [], "error": str(e)})

@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_get():
    try:
        import json
        wl_path = cfg.DASHVIEW_HOME / "watchlist.json"
        if not wl_path.exists():
            return jsonify({"wallets": []})
        return jsonify({"wallets": json.loads(wl_path.read_text())})
    except Exception as e:
        return jsonify({"wallets": [], "error": str(e)})

@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_post():
    try:
        from flask import request
        import json
        data = request.get_json()
        action = data.get("action")
        address = (data.get("address") or "").lower().strip()
        label = data.get("label", address[:10])
        wl_path = cfg.DASHVIEW_HOME / "watchlist.json"
        wallets = json.loads(wl_path.read_text()) if wl_path.exists() else []
        if action == "add":
            if not address.startswith("0x") or len(address) < 20:
                return jsonify({"ok": False, "error": "Invalid address"})
            if any(w["address"] == address for w in wallets):
                return jsonify({"ok": False, "error": "Already in watchlist"})
            wallets.append({"address": address, "label": label, "added": int(time.time())})
            wl_path.write_text(json.dumps(wallets, indent=1))
            return jsonify({"ok": True})
        elif action == "remove":
            wallets = [w for w in wallets if w["address"] != address]
            wl_path.write_text(json.dumps(wallets, indent=1))
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Unknown action"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/watchlist/pnl")
@with_timeout(20)
def api_watchlist_pnl():
    try:
        import json
        wl_path = cfg.DASHVIEW_HOME / "watchlist.json"
        if not wl_path.exists():
            return jsonify({"wallets": []})
        wallets = json.loads(wl_path.read_text())
        results = []
        from src.data_sources.data_api import DataApiClient
        with DataApiClient(timeout=8) as api:
            for w in wallets[:10]:
                try:
                    import httpx as _hx
                    addr = w["address"]
                    lb = {}
                    try:
                        r = _hx.get("https://lb-api.polymarket.com/portfolio",
                                    params={"address": addr}, timeout=8)
                        if r.status_code == 200:
                            lb = r.json() or {}
                    except Exception:
                        pass
                    all_time = None
                    for key in ("profit","pnl","value","totalProfit","combinedPnl"):
                        if lb.get(key) is not None:
                            try: all_time = float(lb[key]); break
                            except Exception: pass
                    results.append({
                        "address": addr,
                        "label": w.get("label", addr[:10]),
                        "combined": round(all_time or 0, 2),
                        "resolved": round(float(lb.get("resolvedPnl") or all_time or 0), 2),
                        "unrealized": round(float(lb.get("unrealizedPnl") or 0), 2),
                        "positions": int(lb.get("openPositionsCount") or 0),
                    })
                except Exception as e:
                    results.append({"address": w["address"], "label": w.get("label",""), "error": True})
        return jsonify({"wallets": results})
    except Exception as e:
        return jsonify({"wallets": [], "error": str(e)})


import re
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

@app.route("/api/trader/<addr>")
@with_timeout(15)
def api_trader(addr):
    addr = (addr or "").strip().lower()
    if not ADDR_RE.fullmatch(addr):
        return jsonify({"ok": False, "error": "Invalid address"}), 400
    try:
        import httpx
        from src.data_sources.data_api import DataApiClient

        all_time = None
        label = ""
        positions = 0
        predictions = 0
        REQUEST_TIMEOUT = 8

        # Source 1: check screener results (verified_passers.json)
        screen_dir = cfg.SCREEN_DIR
        for fname in ("verified_passers.json", "all_verified_passers.json", "passers.json"):
            fpath = screen_dir / fname
            if not fpath.exists():
                continue
            try:
                data = json.loads(fpath.read_text()) or []
                for w in data:
                    if (w.get("address") or "").lower() == addr:
                        all_time = float(w.get("all_time_profit") or w.get("pnl") or 0)
                        predictions = int(w.get("predictions") or w.get("closed") or 0)
                        t2 = w.get("tier2") or {}
                        break
            except Exception:
                pass
            if all_time is not None:
                break

        # Source 2: leaderboard top 50
        if all_time is None:
            try:
                r = httpx.get("https://lb-api.polymarket.com/profit",
                              params={"window": "All", "limit": 50}, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200:
                    for entry in (r.json() or []):
                        if (entry.get("proxyWallet") or "").lower() == addr:
                            all_time = float(entry.get("amount") or 0)
                            label = entry.get("name") or entry.get("pseudonym") or ""
                            break
            except Exception:
                pass

        # Source 2: data-api profile + positions
        with DataApiClient(timeout=8) as api:
            try:
                prof = api.get_profile(addr) or {}
                if not label:
                    label = prof.get("name") or prof.get("pseudonym") or ""
                if not predictions:
                    for key in ("numTrades", "tradesCount", "predictionsCount"):
                        if prof.get(key):
                            predictions = int(prof[key]); break
            except Exception:
                pass
            try:
                pos_list = api.get_positions_by_user(addr) or []
                positions = len([p for p in pos_list if not p.get("redeemed")])
            except Exception:
                pass

        if all_time is None and not label and positions == 0:
            return jsonify({"ok": False, "error": "No Polymarket profile found for that address."}), 404

        return jsonify({
            "ok": True,
            "address": addr,
            "label": label,
            "combined": round(all_time or 0, 2),
            "resolved": round(all_time or 0, 2),
            "unrealized": 0,
            "positions": positions,
            "predictions": predictions,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

@app.route("/api/watchlist/export")
def api_watchlist_export():
    from flask import Response, request
    import json, csv, io
    wl_path = cfg.DASHVIEW_HOME / "watchlist.json"
    wallets = json.loads(wl_path.read_text()) if wl_path.exists() else []
    if request.args.get("format") == "json":
        return Response(json.dumps(wallets, indent=2), mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=watchlist.json"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["address", "label", "added"])
    for x in wallets:
        w.writerow([x.get("address",""), x.get("label",""), x.get("added","")])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=watchlist.csv"})

@app.route("/api/watchlist/import", methods=["POST"])
def api_watchlist_import():
    from flask import request
    import json, csv, io
    MAX_ROWS = 500
    data = request.get_json(force=True, silent=True) or {}
    rows = []
    if isinstance(data.get("wallets"), list):
        rows = [(str(x.get("address","")), str(x.get("label",""))) for x in data["wallets"]]
    elif data.get("csv"):
        for i, r in enumerate(csv.reader(io.StringIO(data["csv"]))):
            if not r: continue
            if i == 0 and r[0].strip().lower() in ("address", "wallet", "addr"): continue
            rows.append((r[0] if len(r) > 0 else "", r[1] if len(r) > 1 else ""))
    else:
        return jsonify({"ok": False, "error": "Provide 'wallets' (array) or 'csv' (text)"}), 400
    wl_path = cfg.DASHVIEW_HOME / "watchlist.json"
    wallets = json.loads(wl_path.read_text()) if wl_path.exists() else []
    have = {w["address"].lower() for w in wallets}
    added = skipped = bad = 0
    for addr, label in rows[:MAX_ROWS]:
        addr = addr.strip().lower()
        if not ADDR_RE.fullmatch(addr): bad += 1; continue
        if addr in have: skipped += 1; continue
        label = (label or "").strip()[:40]
        wallets.append({"address": addr, "label": label or addr[:10], "added": int(time.time())})
        have.add(addr); added += 1
    wl_path.write_text(json.dumps(wallets, indent=1))
    return jsonify({"ok": True, "added": added, "skipped": skipped, "invalid": bad, "total": len(wallets)})

@app.route("/api/config", methods=["GET"])
def api_config_get():
    try:
        import json
        cfg_path = cfg.DASHVIEW_HOME / "config.json"
        if not cfg_path.exists():
            return jsonify({
                "bot_name": os.environ.get("BOT_NAME", "Atlas"),
                "bot_dir": str(BOT_DIR),
                "domain": "YOUR_DOMAIN.duckdns.org",
                "port": 443,
            })
        return jsonify(json.loads(cfg_path.read_text()))
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/config", methods=["POST"])
def api_config_save():
    try:
        from flask import request
        import json, subprocess
        data = request.get_json()
        cfg_path = Path("/opt/dashview/config.json")
        cfg_path.write_text(json.dumps(data, indent=2))

        # Update systemd service with new BOT_DIR and BOT_NAME
        bot_dir = data.get("bot_dir", "/opt/polymarket-bot")
        bot_name = data.get("bot_name", "Atlas")
        port = data.get("port", 443)

        service = f"""[Unit]
Description=DashView Mobile Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={str(cfg.DASHVIEW_HOME)}
ExecStart={str(cfg.BOT_DIR)}/.venv/bin/python {str(cfg.DASHVIEW_HOME)}/server.py --port {port}
Restart=always
RestartSec=5
Environment=BOT_DIR={bot_dir}
Environment=BOT_NAME={bot_name}
Environment=DASHVIEW_HOME={str(cfg.DASHVIEW_HOME)}
Environment=CAPITAL={cfg.CAPITAL}
Environment=RISK_PCT={cfg.RISK_PCT * 100}
Environment=SSL_CERT_PATH={cfg.SSL_CERT}
Environment=SSL_KEY_PATH={cfg.SSL_KEY}
Environment=DOMAIN={data.get("domain", "")}

[Install]
WantedBy=multi-user.target
"""
        Path("/etc/systemd/system/dashview.service").write_text(service)
        subprocess.run(["systemctl", "daemon-reload"])
        subprocess.Popen(["systemctl", "restart", "dashview"])
        return jsonify({"ok": True, "message": "Config saved. Restarting DashView…"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/simulation")
@with_timeout(20)
def api_simulation():
    try:
        import json
        from datetime import datetime
        log_path = BOT_DIR / "logs" / "shadow_decisions.jsonl"
        if not log_path.exists():
            return jsonify({"error": "No log file"})

        from flask import request
        # Capital can be passed as URL param or from config
        cfg_path = Path("/opt/dashview/config.json")
        capital = 500.0
        risk_pct = 0.015
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text())
                capital = float(cfg.get("capital", 500))
                risk_pct = float(cfg.get("risk_pct", 1.5)) / 100
            except Exception as e:
                pass
        # URL params override config
        if request.args.get("capital"):
            try:
                capital = float(request.args.get("capital"))
            except Exception as e:
                pass
        if request.args.get("risk"):
            try:
                risk_pct = float(request.args.get("risk")) / 100
            except Exception as e:
                pass

        bet_size = round(capital * risk_pct, 2)
        max_positions = int(capital / bet_size)

        events = []
        with open(log_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("decision") in ("copy", "shadow_resolution"):
                        events.append(d)
                except Exception as e:
                    pass

        events.sort(key=lambda x: int(x.get("timestamp", 0)))

        open_pos = {}
        capital_used = 0.0
        placed = wins = losses = skipped = 0
        pnl = 0.0
        daily_pnl = {}
        trader_stats = {}

        for e in events:
            cid = e.get("condition_id", "")
            decision = e.get("decision", "")
            trader = e.get("trader", "unknown")
            ts = int(e.get("timestamp", 0))
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

            if trader not in trader_stats:
                trader_stats[trader] = {"placed": 0, "skipped": 0, "wins": 0, "losses": 0, "pnl": 0.0}

            if decision == "copy":
                if capital_used + bet_size <= capital and cid not in open_pos:
                    open_pos[cid] = trader
                    capital_used += bet_size
                    placed += 1
                    trader_stats[trader]["placed"] += 1
                else:
                    skipped += 1
                    trader_stats[trader]["skipped"] += 1
            elif decision == "shadow_resolution" and cid in open_pos:
                orig_trader = open_pos[cid]
                p = e.get("realized_pnl_override", e.get("pnl", 0)) or 0
                scaled = p * (bet_size / 100.0)
                pnl += scaled
                capital_used -= bet_size
                capital_used += scaled
                del open_pos[cid]
                daily_pnl[day] = daily_pnl.get(day, 0) + scaled
                trader_stats[orig_trader]["pnl"] += scaled
                if p > 0:
                    wins += 1
                    trader_stats[orig_trader]["wins"] += 1
                elif p < 0:
                    losses += 1
                    trader_stats[orig_trader]["losses"] += 1

        total = wins + losses
        points = []
        cum = 0
        for day in sorted(daily_pnl.keys()):
            cum += daily_pnl[day]
            points.append({"date": day, "daily": round(daily_pnl[day], 2), "cumulative": round(cum, 2)})

        traders_list = []
        for t, s in trader_stats.items():
            t_total = s["wins"] + s["losses"]
            traders_list.append({
                "trader": t,
                "placed": s["placed"],
                "skipped": s["skipped"],
                "wins": s["wins"],
                "losses": s["losses"],
                "win_rate": round(s["wins"] / t_total * 100, 1) if t_total else 0,
                "pnl": round(s["pnl"], 2),
            })
        traders_list.sort(key=lambda x: x["pnl"], reverse=True)

        return jsonify({
            "capital": capital,
            "bet_size": bet_size,
            "max_positions": max_positions,
            "placed": placed,
            "skipped": skipped,
            "resolved": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "pnl": round(pnl, 2),
            "final_capital": round(capital + pnl, 2),
            "return_pct": round(pnl / capital * 100, 1),
            "points": points,
            "traders": traders_list,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    import ssl as _ssl
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=443)
    args = parser.parse_args()
    cert = cfg.SSL_CERT
    key  = cfg.SSL_KEY
    if Path(cert).exists() and Path(key).exists():
        try:
            context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            domain = os.environ.get("DOMAIN", "your-domain")
            logger.info(f"DashView HTTPS running at https://{domain}:{args.port}")
            app.run(host=args.host, port=args.port, ssl_context=context, debug=False)
        except Exception as e:
            logger.error(f"HTTPS failed: {e} — falling back to HTTP on port 8080")
            app.run(host=args.host, port=8080, debug=False)
    else:
        logger.warning(f"SSL cert not found at {cert} — running HTTP on port 8080")
        logger.warning("Set SSL_CERT_PATH and SSL_KEY_PATH env vars for HTTPS")
        app.run(host=args.host, port=8080, debug=False)

@app.route("/api/events")
def api_events():
    """Return recent events for notification polling."""
    try:
        from flask import request
        import json
        since = int(request.args.get("since", 0))
        log_path = BOT_DIR / "logs" / "shadow_decisions.jsonl"
        events = []

        if log_path.exists():
            with open(log_path) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception as e:
                        continue
                    ts = int(d.get("timestamp", 0))
                    if ts <= since:
                        continue
                    decision = d.get("decision", "")
                    trader = d.get("trader", "")
                    question = d.get("question", "")
                    outcome = d.get("outcome", "")

                    if decision == "copy":
                        events.append({"type": "copied", "timestamp": ts, "trader": trader, "question": question, "outcome": outcome, "price": d.get("their_price", 0)})
                    elif decision == "shadow_resolution":
                        pnl = d.get("pnl", d.get("our_pnl", 0)) or 0
                        if abs(pnl) > 50:
                            events.append({"type": "big_win" if pnl > 0 else "big_loss", "timestamp": ts, "trader": trader, "question": question, "outcome": outcome, "pnl": round(pnl, 2)})

        # Check bot status
        try:
            import subprocess
            result = subprocess.run(["systemctl", "is-active", "tradingbot-copy-bot"], capture_output=True, text=True)
            bot_running = result.stdout.strip() == "active"
            events.append({"type": "bot_status", "timestamp": int(time.time()), "running": bot_running})
        except Exception as e:
            pass

        # Check live trades from pnl cache
        try:
            from src.pnl_cache import get_cached_pnl
            pnl = get_cached_pnl()
            if pnl.realized_wins > 0 or pnl.realized_losses > 0:
                events.append({"type": "live_status", "timestamp": int(time.time()), "wins": pnl.realized_wins, "losses": pnl.realized_losses, "pnl": round(pnl.realized_pnl, 2)})
        except Exception as e:
            pass

        return jsonify({"events": events[-50:], "server_time": int(time.time())})
    except Exception as e:
        return jsonify({"events": [], "error": str(e)})

# SSL context for HTTPS
import ssl as _ssl
def run_https():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=443)
    args = parser.parse_args()
    context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cfg.SSL_CERT, cfg.SSL_KEY)
    domain = os.environ.get("DOMAIN", "your-domain")
    logger.info(f"DashView HTTPS running at https://{domain}:{args.port}")
    app.run(host=args.host, port=args.port, ssl_context=context, debug=False)

