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

    # Domain (used for displayed config + push notification claims)
    DOMAIN        = os.environ.get("DOMAIN", "YOUR_DOMAIN.duckdns.org")

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

def resolve_polymarket_name(address, fallback=None):
    """Try to resolve a wallet's real Polymarket display name via the
    leaderboard profit endpoint (lb-api.polymarket.com/profit) — this is
    the only known-working source for usernames as of Jun 2026, since
    data-api.polymarket.com/profile and lb-api.polymarket.com/portfolio
    are both confirmed dead (404) since 2026-06-11. Works for low-volume
    wallets too, not just leaderboard-caliber ones (confirmed via testing
    Jun 24/25: resolved names for wallets with as little as ~$58 profit).

    Returns the resolved name, or `fallback` if resolution fails, the
    response is empty, or the only available name is Polymarket's own
    auto-generated "0xADDRESS-timestamp" placeholder (not a real chosen
    username — happens for wallets that never set a custom display name).

    Short timeout (4s) and broad exception handling so a slow or dead
    upstream call never blocks a promote/demote action — this is a
    nice-to-have enrichment, not a required step."""
    import re
    try:
        import httpx
        url = f"https://lb-api.polymarket.com/profit?address={address}&window=All&limit=1"
        r = httpx.get(url, timeout=4.0, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list) and len(data) > 0:
                name = data[0].get("name") or data[0].get("pseudonym")
                if name and not re.match(r"^0x[a-fA-F0-9]+-\d+$", name):
                    return name
    except Exception as e:
        logger.error(f"resolve_polymarket_name failed for {address}: {e}")
    return fallback


def get_shadow_pnl():
    try:
        from src.pnl_cache import get_cached_shadow_pnl
        from src.shadow_list import ShadowList
        shadow = get_cached_shadow_pnl()
        all_names = [e.name for e in ShadowList().list_all()]
        result = []
        for name in all_names:
            s = shadow.get(name)
            addr = ""
            try:
                all_traders = ShadowList().list_all()
                for t in all_traders:
                    if t.name == name:
                        addr = t.address
                        break
            except Exception:
                pass
            if s:
                # Combine BOTH win/loss buckets — realized_wins/losses only
                # count SELL-based closes, while resolution_wins/losses
                # (added 2026-06-25) count RESOLUTION-based closes. Most
                # copy-traded wallets hold to resolution rather than
                # actively selling, so using only the sell-based bucket
                # produced a misleading 0% win rate for wallets with real,
                # substantial resolution-based profit (e.g. soarin22:
                # $308.94 resolved profit, 14W/5L resolution closes, but
                # 0/0 sell-based closes — confirmed via direct testing).
                wins = s.realized_wins + s.resolution_wins
                losses = s.realized_losses + s.resolution_losses
                total = wins + losses
                win_rate = round(wins / total * 100, 1) if total else None
                result.append({"name": name, "address": addr, "combined": round(s.combined_hypothetical_pnl,2), "resolved": round(s.resolution_realized_pnl,2), "unrealized": round(s.hypothetical_unrealized_pnl,2), "open_positions": s.open_positions_count, "wins": wins, "losses": losses, "win_rate": win_rate, "active": True})
            else:
                result.append({"name": name, "address": addr, "combined": 0, "resolved": 0, "unrealized": 0, "open_positions": 0, "wins": 0, "losses": 0, "win_rate": None, "active": False})
        return result
    except Exception as e:
        logger.error(f"get_shadow_pnl failed: {e}")
        return [{"error": str(e)}]

def get_live_pnl():
    try:
        from src.pnl_cache import get_cached_pnl
        pnl = get_cached_pnl()
        # Cost basis (invested_in_open) was already computed in _compute()
        # and returned on CachedPnL, just never exposed via this API
        # function. Current mark-to-market value is a trivial derived sum
        # (cost basis + unrealized P&L) — not a separately stored field,
        # since unrealized_pnl is defined as the gain/loss relative to
        # cost basis.
        cost_basis = round(pnl.invested_in_open, 2)
        mark_to_market_value = round(pnl.invested_in_open + pnl.unrealized_pnl, 2)
        closed_cost_basis = round(pnl.closed_cost_basis, 2)
        closed_proceeds = round(pnl.closed_proceeds, 2)
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
            "open_positions_cost_basis": cost_basis,
            "open_positions_mark_to_market": mark_to_market_value,
            "closed_cost_basis": closed_cost_basis,
            "closed_proceeds": closed_proceeds,
        }
    except Exception as e:
        logger.error(f"get_live_pnl failed: {e}")
        return {"error": str(e)}

def get_live_pnl_by_wallet():
    """Per-trader breakdown of live-tier P&L, built client-side from the
    same CachedPnL.closed / open_positions_detail data get_live_pnl()
    already uses in aggregate. Does not touch pnl_cache.py / bot core
    code at all — pure read-only grouping of data that already exists."""
    try:
        from src.pnl_cache import get_cached_pnl
        pnl = get_cached_pnl()
        by_trader = {}

        for c in pnl.closed:
            t = c.trader
            if t not in by_trader:
                by_trader[t] = {"realized": 0.0, "wins": 0, "losses": 0, "open_positions": 0, "unrealized": 0.0}
            by_trader[t]["realized"] += c.realized_pnl
            if c.realized_pnl > 0:
                by_trader[t]["wins"] += 1
            elif c.realized_pnl < 0:
                by_trader[t]["losses"] += 1

        for o in pnl.open_positions_detail:
            t = o.trader
            if t not in by_trader:
                by_trader[t] = {"realized": 0.0, "wins": 0, "losses": 0, "open_positions": 0, "unrealized": 0.0}
            by_trader[t]["open_positions"] += 1
            if o.unrealized_pnl is not None:
                by_trader[t]["unrealized"] += o.unrealized_pnl

        # Resolve trader NAME -> address via roster.yaml (the live-tier
        # source of truth) so the frontend can act on these wallets (e.g.
        # demote to shadow) without only having a display name to go on.
        name_to_address = {}
        try:
            import yaml as _yaml
            roster_path = cfg.BOT_DIR / "config" / "roster.yaml"
            if roster_path.exists():
                roster = _yaml.safe_load(roster_path.read_text()) or []
                if isinstance(roster, list):
                    for w in roster:
                        nm = w.get("name")
                        addr = w.get("address")
                        if nm and addr:
                            name_to_address[nm] = addr
        except Exception as e:
            logger.error(f"get_live_pnl_by_wallet: roster address lookup failed: {e}")

        result = []
        for trader, stats in by_trader.items():
            total = stats["wins"] + stats["losses"]
            result.append({
                "trader": trader,
                "address": name_to_address.get(trader),
                "realized": round(stats["realized"], 2),
                "unrealized": round(stats["unrealized"], 2),
                "combined": round(stats["realized"] + stats["unrealized"], 2),
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": round(stats["wins"] / total * 100, 1) if total else 0,
                "open_positions": stats["open_positions"],
            })
        result.sort(key=lambda x: x["combined"], reverse=True)
        return result
    except Exception as e:
        logger.error(f"get_live_pnl_by_wallet failed: {e}")
        return []

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
        # Read from all_verified_passers.json (the CUMULATIVE record across
        # every batch of a run), not verified_passers.json (which only ever
        # holds the MOST RECENT batch's results and gets overwritten every
        # batch — confirmed 2026-06-24 this was silently showing stale/
        # incomplete data, e.g. only the last empty batch's "0 candidates"
        # instead of the real accumulated results). Fall back to
        # verified_passers.json only if the cumulative file doesn't exist
        # yet (e.g. a screen that hasn't completed a full pipeline run).
        vpath = screen_dir / "all_verified_passers.json"
        if not vpath.exists():
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
                    "tier2": tier2,
                    "tier1_only": tier1_only,
                    "wallets": data,
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
        "live_pnl_by_wallet": get_live_pnl_by_wallet(),
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

@app.route("/api/screener-config", methods=["GET"])
def api_screener_config_get():
    try:
        import yaml
        yml_path = cfg.BOT_DIR / "scripts" / "pm-screen.yml"
        if not yml_path.exists():
            return jsonify({"ok": False, "error": "pm-screen.yml not found"}), 404
        data = yaml.safe_load(yml_path.read_text()) or {}
        f = data.get("filters", {})
        d = data.get("discovery", {})
        ds = data.get("deep_screen", {})
        t2 = data.get("tier2_filters", {})

        # Load defaults
        defaults_path = cfg.DASHVIEW_HOME / "screener_defaults.json"
        defaults = json.loads(defaults_path.read_text()) if defaults_path.exists() else {}

        return jsonify({
            "ok": True,
            "filters": {
                "min_win_rate": f.get("min_win_rate", 0.60),
                "min_closed_markets": f.get("min_closed_markets", 10),
                "max_two_sided_ratio": f.get("max_two_sided_ratio", 0.40),
                "min_trades_30d": f.get("min_trades_30d", 10),
                "max_trades_30d": f.get("max_trades_30d", 5000),
                "min_median_usd": f.get("min_median_usd", 10.0),
                "max_median_usd": f.get("max_median_usd", 5000.0),
                "max_days_since_trade": f.get("max_days_since_trade", 14),
                "min_closed_pnl": f.get("min_closed_pnl", 200.0),
            },
            "discovery": {
                "n_markets": d.get("n_markets", 120),
            },
            "deep_screen": {
                "n_deep": ds.get("n_deep", 99999),
                "pages": ds.get("pages", 5),
            },
            "tier2_filters": {
                "min_bucket_wr": t2.get("min_bucket_wr", 0.55),
                "min_concentration": t2.get("min_concentration", 0.25),
                "min_hold_days": t2.get("min_hold_days", 1.0),
                "max_drawdown_ratio": t2.get("max_drawdown_ratio", 5.0),
                "max_size_cv": t2.get("max_size_cv", 2.0),
                "min_recent_trades": t2.get("min_recent_trades", 5),
            },
            "defaults": defaults,
        })
    except Exception as e:
        logger.error(f"screener_config_get failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/screener-config", methods=["POST"])
def api_screener_config_save():
    try:
        from flask import request
        import yaml
        data = request.get_json(force=True, silent=True) or {}
        save_as_default = data.get("save_as_default", False)
        yml_path = cfg.BOT_DIR / "scripts" / "pm-screen.yml"

        # Load existing yml to preserve comments structure
        existing = yaml.safe_load(yml_path.read_text()) if yml_path.exists() else {}

        f = data.get("filters", {})
        d = data.get("discovery", {})
        ds = data.get("deep_screen", {})
        t2 = data.get("tier2_filters", {})

        if f:
            existing.setdefault("filters", {}).update({
                k: v for k, v in f.items() if v is not None
            })
        if d:
            existing.setdefault("discovery", {}).update({
                k: v for k, v in d.items() if v is not None
            })
        if ds:
            existing.setdefault("deep_screen", {}).update({
                k: v for k, v in ds.items() if v is not None
            })
        if t2:
            existing.setdefault("tier2_filters", {}).update({
                k: v for k, v in t2.items() if v is not None
            })

        yml_path.write_text(yaml.dump(existing, default_flow_style=False))
        logger.info(f"screener config saved (save_as_default={save_as_default})")

        # Save defaults if requested
        if save_as_default:
            defaults_path = cfg.DASHVIEW_HOME / "screener_defaults.json"
            defaults_path.write_text(json.dumps(data, indent=2))

        return jsonify({"ok": True, "saved_as_default": save_as_default})
    except Exception as e:
        logger.error(f"screener_config_save failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

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

@app.route("/api/run-scanner-deep", methods=["POST"])
def api_run_scanner_deep():
    """DEEP DISCOVERY MODE: screens ALL active markets (~2,100) instead of
    just the top n_markets by volume — surfaces a much larger candidate
    pool (151,816 raw wallets / ~24,500 human-size in testing, vs ~21,900/
    1,135 in default mode). Takes much longer (~9 min discovery alone, then
    hours for the full Tier 1/2 screen on the larger pool) — meant for
    occasional deliberate runs via this button, not the automated cron job.
    Logs to a SEPARATE file from the normal scanner so deep runs don't mix
    into and confuse the regular scan history."""
    try:
        import subprocess
        subprocess.Popen(
            ["bash", "-c", f"cd {BOT_DIR} && rm -f /tmp/screen-v3/discovered.json && ./scripts/run_pipeline.sh --deep >> /tmp/screen-v3/cron_deep.log 2>&1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Deep scanner failed: {e}")
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
                        "config_used": m.get("config_used", {}),
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
        name = data.get("name") or ""
        mode = data.get("mode", "shadow")  # "shadow" or "live"
        # If the caller didn't supply a real name (or only gave us an
        # address-prefix-style placeholder), try to resolve the wallet's
        # actual Polymarket display name before falling back further.
        if not name or name.lower().startswith(address[:8].lower()):
            name = resolve_polymarket_name(address, fallback=name or address[:8])

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
            from datetime import datetime, timezone
            st_path = cfg.BOT_DIR / ".tradingbot" / "shadow_traders.json"
            st_path.parent.mkdir(parents=True, exist_ok=True)
            traders = json.loads(st_path.read_text()) if st_path.exists() else []
            if not any(t.get("address","").lower() == address for t in traders):
                traders.append({"address": address, "name": name, "added_date": datetime.now(timezone.utc).isoformat(), "source_screen_id": "dashview_promote", "active": True, "activity_status": None, "notes": "Added via DashView promote button."})
                st_path.write_text(json.dumps(traders, indent=2))
            logger.info(f"Promoted {name} ({address}) to shadow")
            return jsonify({"ok": True, "message": f"Added {name} to shadow list"})

        elif mode == "live":
            # Add to roster.yaml — must be a FLAT LIST at the top level,
            # matching roster_loader.py's required schema (NOT a dict with
            # a "traders" key — that was the original bug, fixed Jun 24).
            roster = []
            if roster_path.exists():
                roster = yaml.safe_load(roster_path.read_text()) or []
            if not isinstance(roster, list):
                return jsonify({"ok": False, "error": "roster.yaml is not a flat list — refusing to write, check file manually"})
            for w in roster:
                if w.get("address","").lower() == address:
                    return jsonify({"ok": False, "error": "Already in roster"})
            # Conservative default sizing — promoting via this button should
            # never produce a live-tier entry with NO sizing at all. $5.00
            # matches the bot's own hard minimum trade floor (kelly_below_min_size),
            # so this is the smallest sane default; revise manually in roster.yaml
            # before relying on this for a real (non-dry-run) promotion.
            # added_date is REQUIRED by roster_loader.py (REQUIRED_FIELDS) —
            # confirmed via direct loader test Jun 24, omitting it causes the
            # bot to reject the whole file on next restart.
            from datetime import datetime, timezone
            roster.append({
                "name": name,
                "address": address,
                "tier": "live",
                "active": True,
                "added_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "sizing": {"base_usd": 5.0},
            })
            roster_path.write_text(yaml.dump(roster, default_flow_style=False))
            # CRITICAL: roster.yaml is only read at bot STARTUP — confirmed
            # repeatedly (TRADER_SIZING, TRADER_STOP_LOSS_OVERRIDES, LIVE
            # traders list all loaded once). Without restarting here, this
            # promotion would silently do NOTHING until some unrelated
            # future restart happens to occur — discovered Jun 25 via the
            # exact same gap in /api/demote-to-shadow (a wallet kept
            # trading live for ~2 hours after being "demoted" because
            # nothing restarted the bot to pick up the roster.yaml change).
            import subprocess as _subprocess
            try:
                _subprocess.run(
                    ["systemctl", "restart", "tradingbot-copy-bot", "tradingbot-telegram-bot"],
                    timeout=15, capture_output=True, text=True,
                )
            except Exception as _e:
                logger.error(f"api_promote live: bot restart failed: {_e}")
            return jsonify({"ok": True, "message": f"Added {name} to live roster with default $5.00 sizing and restarted the bot — review/adjust sizing before relying on this for real trading"})

        return jsonify({"ok": False, "error": "Invalid mode"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/version")
def api_version():
    try:
        version_path = cfg.DASHVIEW_HOME / "VERSION"
        current = version_path.read_text().strip() if version_path.exists() else "unknown"
        # Check GitHub for latest version
        import httpx
        latest = None
        try:
            r = httpx.get("https://api.github.com/repos/harryosman1/DashView/releases/latest",
                         timeout=5, headers={"Accept": "application/vnd.github.v3+json"})
            if r.status_code == 200:
                latest = r.json().get("tag_name", "").lstrip("v")
        except Exception:
            pass
        update_available = False
        if latest and current != "unknown":
            try:
                cv = [int(x) for x in current.split(".")]
                lv = [int(x) for x in latest.split(".")]
                update_available = lv > cv
            except Exception:
                update_available = latest != current
        return jsonify({
            "ok": True,
            "current": current,
            "latest": latest,
            "update_available": update_available,
        })
    except Exception as e:
        logger.error(f"api_version failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/update", methods=["POST"])
def api_update():
    try:
        import subprocess
        dashview_home = str(cfg.DASHVIEW_HOME)
        # Check it's a git repo
        result = subprocess.run(["git", "-C", dashview_home, "status"],
                               capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify({"ok": False, "error": "Not a git repository — cannot auto-update"}), 400
        # Pull latest
        pull = subprocess.run(["git", "-C", dashview_home, "pull", "origin", "main"],
                             capture_output=True, text=True, timeout=30)
        if pull.returncode != 0:
            return jsonify({"ok": False, "error": pull.stderr.strip()}), 500
        output = pull.stdout.strip()
        logger.info(f"git pull output: {output}")
        # Restart dashview after a short delay
        subprocess.Popen(["bash", "-c", "sleep 2 && systemctl restart dashview"])
        return jsonify({"ok": True, "output": output, "restarting": True})
    except Exception as e:
        logger.error(f"api_update failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

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

@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")

@app.route("/sw.js")
def service_worker():
    resp = app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

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
        for w in wallets[:10]:
            addr = w["address"]
            try:
                # Reuse api_trader's proven multi-source lookup (screener cache
                # -> leaderboard top 50 -> DataApiClient) instead of the old
                # broken /portfolio endpoint call, which 404'd for every wallet.
                resp = api_trader(addr)
                # api_trader returns a Flask Response from jsonify(); unwrap it.
                payload = json.loads(resp[0].get_data(as_text=True)) if isinstance(resp, tuple) else json.loads(resp.get_data(as_text=True))
                if not payload.get("ok"):
                    results.append({"address": addr, "label": w.get("label", addr[:10]), "error": True})
                    continue
                results.append({
                    "address": addr,
                    "label": w.get("label") or payload.get("label") or addr[:10],
                    "combined": payload.get("combined", 0),
                    "resolved": payload.get("resolved", 0),
                    "unrealized": payload.get("unrealized", 0),
                    "positions": payload.get("positions", 0),
                })
            except Exception:
                results.append({"address": addr, "label": w.get("label",""), "error": True})
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

        # Source 2: data-api positions (note: get_profile's /profile endpoint
        # was retired by Polymarket — confirmed 404 directly via curl on
        # 2026-06-22, both as ?user= query param and as a /profile/<addr>
        # path. DO NOT re-add a call to it: data_api.py's _get() retries
        # 404s up to 6x with exponential backoff, so a dead endpoint there
        # silently burns 30+ seconds per wallet. /positions still works and
        # already contains real realizedPnl/cashPnl per position, which is
        # what we use below instead.)
        resolved_pnl = 0.0
        unrealized_pnl = 0.0
        with DataApiClient(timeout=8) as api:
            try:
                pos_list = api.get_positions_by_user(addr) or []
                positions = len([p for p in pos_list if not p.get("redeemable")])
                for p in pos_list:
                    try:
                        resolved_pnl += float(p.get("realizedPnl") or 0)
                        if not p.get("redeemable"):
                            unrealized_pnl += float(p.get("cashPnl") or 0)
                    except (TypeError, ValueError):
                        continue
            except Exception:
                pass

        combined = resolved_pnl + unrealized_pnl
        if all_time is None:
            all_time = combined

        if all_time == 0 and not label and positions == 0:
            return jsonify({"ok": False, "error": "No Polymarket profile found for that address."}), 404

        return jsonify({
            "ok": True,
            "address": addr,
            "label": label,
            "combined": round(combined, 2),
            "resolved": round(resolved_pnl, 2),
            "unrealized": round(unrealized_pnl, 2),
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
                "domain": cfg.DOMAIN,
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

        selected_wallets = None
        if request.args.get("wallets"):
            selected_wallets = set(w.strip() for w in request.args.get("wallets").split(",") if w.strip())

        bet_size = round(capital * risk_pct, 2)
        max_positions = int(capital / bet_size)

        events = []
        with open(log_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("decision") in ("copy", "shadow_resolution"):
                        if selected_wallets is None or d.get("trader", "unknown") in selected_wallets:
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


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    try:
        from flask import request as _req
        settings_path = cfg.DASHVIEW_HOME / "user_settings.json"
        if _req.method == "GET":
            if settings_path.exists():
                return jsonify({"ok": True, "settings": json.loads(settings_path.read_text())})
            return jsonify({"ok": True, "settings": {}})
        else:
            data = _req.get_json()
            existing = json.loads(settings_path.read_text()) if settings_path.exists() else {}
            existing.update(data)
            settings_path.write_text(json.dumps(existing, indent=2))
            return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"api_settings failed: {e}")
        return jsonify({"ok": False, "error": str(e)})

VAPID_PUBLIC_KEY = "BDkcs3R5oXiS2y2skKXavkd4MAKFAFSbPJOTJXE21w3il5W1xjXqbJrVhkub2R0u1ZHquXAYsYagUtqzJo-z1_0"
VAPID_PRIVATE_KEY_PATH = "/opt/dashview/vapid_private.pem"
PUSH_SUBS_PATH = cfg.DASHVIEW_HOME / "push_subscriptions.json"

def load_push_subs():
    try:
        if PUSH_SUBS_PATH.exists():
            return json.loads(PUSH_SUBS_PATH.read_text())
    except Exception:
        pass
    return []

def save_push_subs(subs):
    PUSH_SUBS_PATH.write_text(json.dumps(subs, indent=2))

def send_push_notification(title, body, tag="dashview"):
    try:
        from pywebpush import webpush, WebPushException
        subs = load_push_subs()
        dead = []
        for sub in subs:
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps({"title": title, "body": body, "tag": tag}),
                    vapid_private_key=VAPID_PRIVATE_KEY_PATH,
                    vapid_claims={"sub": f"mailto:admin@{cfg.DOMAIN}"}
                )
            except WebPushException as e:
                if e.response and e.response.status_code in (404, 410):
                    dead.append(sub)
                logger.warning(f"Push failed: {e}")
            except Exception as e:
                logger.warning(f"Push error: {e}")
        if dead:
            subs = [s for s in subs if s not in dead]
            save_push_subs(subs)
    except Exception as e:
        logger.error(f"send_push_notification failed: {e}")

@app.route("/api/push/vapid-public-key")
def api_vapid_key():
    return jsonify({"ok": True, "key": VAPID_PUBLIC_KEY})

@app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    try:
        from flask import request as _req
        sub = _req.get_json()
        if not sub or "endpoint" not in sub:
            return jsonify({"ok": False, "error": "Invalid subscription"})
        subs = load_push_subs()
        if not any(s.get("endpoint") == sub["endpoint"] for s in subs):
            subs.append(sub)
            save_push_subs(subs)
            logger.info(f"New push subscription: {sub['endpoint'][:50]}...")
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"push subscribe failed: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/push/unsubscribe", methods=["POST"])
def api_push_unsubscribe():
    try:
        from flask import request as _req
        data = _req.get_json()
        endpoint = data.get("endpoint", "")
        subs = load_push_subs()
        subs = [s for s in subs if s.get("endpoint") != endpoint]
        save_push_subs(subs)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/demote-to-shadow", methods=["POST"])
def api_demote_to_shadow():
    """Demote a LIVE-tier wallet back to shadow tracking. Unlike /api/demote
    (which removes a wallet from EVERY list — shadow_list.yaml,
    shadow_traders.json, AND roster.yaml), this is a compose action matching
    the existing demoteToWatch pattern: ADD to the destination tier first,
    THEN remove from the source tier (roster.yaml live entry). This keeps
    the wallet under shadow observation rather than dropping it entirely."""
    try:
        import yaml as _yaml
        from flask import request as _req
        from datetime import datetime, timezone
        data = _req.get_json()
        address = (data.get("address") or "").lower().strip()
        name = data.get("name") or ""
        if not address:
            return jsonify({"ok": False, "error": "No address provided"})
        # If the caller didn't supply a real name (or only gave us an
        # address-prefix-style placeholder, e.g. from a live-tier card
        # where the name happens to be an address), try to resolve the
        # wallet's actual Polymarket display name first.
        if not name or name.lower().startswith(address[:8].lower()):
            name = resolve_polymarket_name(address, fallback=name or address[:8])

        # 1. Add to shadow_traders.json if not already present
        st_path = cfg.BOT_DIR / ".tradingbot" / "shadow_traders.json"
        st_path.parent.mkdir(parents=True, exist_ok=True)
        traders = json.loads(st_path.read_text()) if st_path.exists() else []
        already_shadow = any(t.get("address","").lower() == address for t in traders)
        if not already_shadow:
            traders.append({
                "address": address,
                "name": name,
                "added_date": datetime.now(timezone.utc).isoformat(),
                "source_screen_id": "dashview_demote_from_live",
                "active": True,
                "activity_status": None,
                "notes": "Demoted from live tier via DashView.",
            })
            st_path.write_text(json.dumps(traders, indent=2))

        # 2. Remove from roster.yaml (the live-tier file) ONLY — do not
        # touch shadow_list.yaml or anything else, unlike /api/demote.
        roster_path = cfg.BOT_DIR / "config" / "roster.yaml"
        removed_from_live = False
        if roster_path.exists():
            roster = _yaml.safe_load(roster_path.read_text()) or []
            if isinstance(roster, list):
                new_roster = [w for w in roster if w.get("address","").lower() != address]
                if len(new_roster) < len(roster):
                    roster_path.write_text(_yaml.dump(new_roster, default_flow_style=False))
                    removed_from_live = True

        if not removed_from_live:
            return jsonify({"ok": False, "error": "Wallet not found in live roster (roster.yaml) — nothing to demote"})

        # CRITICAL: roster.yaml is only read at bot STARTUP — without
        # restarting here, this wallet would keep being treated as live
        # (sizing, stop-loss overrides, the LIVE traders list — all
        # loaded once) until some unrelated future restart happened to
        # occur. CONFIRMED this exact gap caused a real problem Jun 25:
        # 0xc7e53a was demoted but kept trading live (including a
        # stop_loss_triggered event) for ~2 hours until an unrelated
        # restart for a different feature finally picked up the change.
        import subprocess as _subprocess
        try:
            _subprocess.run(
                ["systemctl", "restart", "tradingbot-copy-bot", "tradingbot-telegram-bot"],
                timeout=15, capture_output=True, text=True,
            )
        except Exception as _e:
            logger.error(f"api_demote_to_shadow: bot restart failed: {_e}")

        logger.info(f"Demoted {name} ({address}) from live to shadow")
        msg = f"Demoted {name} to shadow tracking and restarted the bot" + (" (was already shadow-tracked)" if already_shadow else "")
        return jsonify({"ok": True, "message": msg})
    except Exception as e:
        logger.error(f"Demote-to-shadow failed: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/demote", methods=["POST"])
def api_demote():
    try:
        import yaml as _yaml
        from flask import request as _req
        data = _req.get_json()
        address = (data.get("address") or "").lower().strip()
        if not address:
            return jsonify({"ok": False, "error": "No address provided"})
        removed_from = []
        shadow_list_path = cfg.BOT_DIR / "config" / "shadow_list.yaml"
        if shadow_list_path.exists():
            shadow = _yaml.safe_load(shadow_list_path.read_text()) or []
            new_shadow = [w for w in shadow if w.get("address","").lower() != address]
            if len(new_shadow) < len(shadow):
                shadow_list_path.write_text(_yaml.dump(new_shadow, default_flow_style=False))
                removed_from.append("shadow_list.yaml")
        st_path = cfg.BOT_DIR / ".tradingbot" / "shadow_traders.json"
        if st_path.exists():
            traders = json.loads(st_path.read_text())
            new_traders = [t for t in traders if t.get("address","").lower() != address]
            if len(new_traders) < len(traders):
                st_path.write_text(json.dumps(new_traders, indent=2))
                removed_from.append("shadow_traders.json")
        roster_path = cfg.BOT_DIR / "config" / "roster.yaml"
        if roster_path.exists():
            roster = _yaml.safe_load(roster_path.read_text()) or []
            if isinstance(roster, list):
                new_roster = [w for w in roster if w.get("address","").lower() != address]
                if len(new_roster) < len(roster):
                    roster_path.write_text(_yaml.dump(new_roster, default_flow_style=False))
                    removed_from.append("roster.yaml")
        if removed_from:
            logger.info(f"Demoted {address} from {removed_from}")
            return jsonify({"ok": True, "message": f"Removed from {', '.join(removed_from)}"})
        else:
            return jsonify({"ok": False, "error": "Wallet not found in any list"})
    except Exception as e:
        logger.error(f"Demote failed: {e}")
        return jsonify({"ok": False, "error": str(e)})

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
            # Only scan the tail of the log — file is 90k+ lines, reading it all every
            # 60s poll is far too slow. Recent events are always near the end.
            MAX_TAIL_LINES = 2000
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                file_size = f.tell()
                block = 8192
                data = b""
                lines_found = 0
                pos = file_size
                while pos > 0 and lines_found < MAX_TAIL_LINES:
                    read_size = min(block, pos)
                    pos -= read_size
                    f.seek(pos)
                    data = f.read(read_size) + data
                    lines_found = data.count(b"\n")
                tail_lines = data.decode("utf-8", errors="ignore").splitlines()[-MAX_TAIL_LINES:]

            push_queue = []
            for line in tail_lines:
                try:
                    d = json.loads(line)
                except Exception:
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
                    _price = d.get("their_price", 0)
                    push_queue.append((f"⚡ Trade Copied — {trader}", f"{outcome} @ ${_price:.2f} {question}", "copied"))
                elif decision == "shadow_resolution":
                    pnl = d.get("pnl", d.get("our_pnl", 0)) or 0
                    if abs(pnl) > 50:
                        events.append({"type": "big_win" if pnl > 0 else "big_loss", "timestamp": ts, "trader": trader, "question": question, "outcome": outcome, "pnl": round(pnl, 2)})
                        _icon = "🟢 Big Win" if pnl > 0 else "🔴 Big Loss"
                        _sign = "+" if pnl > 0 else ""
                        push_queue.append((f"{_icon} — {trader}", f"{question} {_sign}{pnl:.0f}", "resolution"))

            # Send pushes in a background thread so the HTTP response never blocks on them
            if push_queue:
                import threading
                def _fire_pushes(items):
                    for title, body, tag in items:
                        try:
                            send_push_notification(title, body, tag=tag)
                        except Exception:
                            pass
                threading.Thread(target=_fire_pushes, args=(push_queue,), daemon=True).start()

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

        # Check if pipeline scan completed recently
        try:
            import re, datetime
            pipeline_log = Path("/tmp/screen-v3/pipeline.log")
            if pipeline_log.exists():
                log_text = pipeline_log.read_text()
                matches = re.findall(r"Pipeline complete: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", log_text)
                if matches:
                    last_complete = matches[-1]
                    dt = datetime.datetime.strptime(last_complete, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                    ts = int(dt.timestamp())
                    if ts > since:
                        t2_matches = re.findall(r"TIER 2 PASS\s+:\s+(\d+)", log_text)
                        t1_matches = re.findall(r"TIER 1 PASS\s+:\s+(\d+)", log_text)
                        t2_count = int(t2_matches[-1]) if t2_matches else 0
                        t1_count = int(t1_matches[-1]) if t1_matches else 0
                        events.append({"type": "scan_complete", "timestamp": ts, "tier2": t2_count, "tier1": t1_count})
                        msg = f"{t2_count} Tier 2 + {t1_count} Tier 1 passers found!" if t2_count > 0 else f"{t1_count} Tier 1 passers found" if t1_count > 0 else "No new passers this scan"
                        send_push_notification("🔍 Screener Scan Complete", msg, tag="scan")
        except Exception:
            pass

        return jsonify({"events": events[-50:], "server_time": int(time.time())})
    except Exception as e:
        return jsonify({"events": [], "error": str(e)})

# SSL context for HTTPS
import ssl as _ssl

# Fix for indefinite hangs: Werkzeug's dev server has no timeout on accepting
# a connection's TLS handshake. A client that opens a connection and never
# completes (or never sends) its handshake (e.g. random internet scanner
# bots probing the port, or a dropped connection mid-handshake) freezes the
# ENTIRE single-threaded accept loop forever — confirmed via py-spy dumps on
# 2026-06-23 showing the main thread stuck in ssl.py do_handshake() with no
# other requests able to be served. Setting WSGIRequestHandler.timeout makes
# Python's underlying socketserver apply that timeout to the handshake
# itself, so a stalled connection gets dropped after N seconds instead of
# blocking everything indefinitely.
from werkzeug.serving import WSGIRequestHandler
WSGIRequestHandler.timeout = 10

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
    app.run(host=args.host, port=args.port, ssl_context=context, debug=False, threaded=True)

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
            app.run(host=args.host, port=args.port, ssl_context=context, debug=False, threaded=True)
        except Exception as e:
            logger.error(f"HTTPS failed: {e} — falling back to HTTP on port 8080")
            app.run(host=args.host, port=8080, debug=False, threaded=True)
    else:
        logger.warning(f"SSL cert not found at {cert} — running HTTP on port 8080")
        logger.warning("Set SSL_CERT_PATH and SSL_KEY_PATH env vars for HTTPS")
        app.run(host=args.host, port=8080, debug=False, threaded=True)

