"""Shadow wallet scanner — verdict engine for promotion/demotion review.

Reads shadow_decisions.jsonl + shadow_traders.json, produces per-wallet
verdicts. RECOMMENDATIONS ONLY — never modifies the roster.

Encoded rules (hard-won, see memory.md Jul 9-10 sessions):
- 30-day window, timestamp-filtered full read (never tail-N)
- Distinct-game grouping via game_key_for_slug (multi-bet-type dedup)
- Live-tier names EXCLUDED (their shadow rows are pre-promotion ghosts)
- Sell-close bucket tracked separately from resolution bucket
- Shadow P&L is denominated at DEFAULT_POSITION_SIZE=$100/copy base
  (~10x live sizing) — absolute $ labeled accordingly, relative
  comparisons valid
- HFT wallets (>800 copies/day) flagged for volume-aware reading
"""
import json
import sys
import pathlib
from datetime import datetime, timezone
from collections import defaultdict

BOT_DIR = pathlib.Path("/opt/polymarket-bot")
sys.path.insert(0, str(BOT_DIR / "fundamentals"))
from outcome_tracker import game_key_for_slug  # noqa: E402

SHADOW_LOG = BOT_DIR / "logs" / "shadow_decisions.jsonl"
SHADOW_ROSTER = BOT_DIR / ".tradingbot" / "shadow_traders.json"
ROSTER_YAML = BOT_DIR / "config" / "roster.yaml"
CACHE_PATH = pathlib.Path("/opt/dashview/scanner_cache.json")

WINDOW_DAYS = 30
RESOLUTIONS = {"shadow_resolution", "shadow_unresolvable"}

# ---- verdict thresholds (data, not buried logic) ----
PROMOTE_MIN_GAMES = 25
PROMOTE_MIN_WR = 60.0
WATCH_MIN_GAMES = 10
DEMOTE_MIN_GAMES = 15          # negative at this sample = demote candidate
DORMANT_DAYS = 14
HFT_COPIES_PER_DAY = 800
HARVESTER_WR = 92.0            # WR this high + thin economics = premium harvester
HARVESTER_MIN_AVG = 6.0
NEW_WALLET_GRACE_DAYS = 7      # no-data wallets younger than this = accumulating, not dead        # $/game floor (at $100 base) below which high-WR = grinder


def _parse_ts(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _live_names():
    """Live-tier names whose shadow rows are pre-promotion ghosts."""
    try:
        import yaml
        roster = yaml.safe_load(ROSTER_YAML.read_text())
        names = {e.get("name") for e in roster if e.get("tier") == "live"}
        # historical live names that may appear in old rows
        names |= {"ferrari_sportmaster", "ferrariChampions2026"}
        return {n for n in names if n}
    except Exception:
        return {"Sportmaster777", "Sportmaster777_wide", "ferrarichampions2026",
                "dv-pm", "Rin2x", "ferrari_sportmaster"}


def scan(now=None):
    now = now or datetime.now(timezone.utc).timestamp()
    cutoff = now - WINDOW_DAYS * 86400

    roster = json.loads(SHADOW_ROSTER.read_text())
    live = _live_names()
    tracked = {e["name"]: e for e in roster if e["name"] not in live}

    stats = defaultdict(lambda: {
        "games": defaultdict(float), "sell_pnl": 0.0, "sell_n": 0,
        "copies": 0, "first": None, "last": None,
    })
    with open(SHADOW_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            tr = d.get("trader")
            if tr not in tracked:
                continue
            ts = _parse_ts(d.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
            s = stats[tr]
            s["first"] = ts if s["first"] is None else min(s["first"], ts)
            s["last"] = ts if s["last"] is None else max(s["last"], ts)
            dec = d.get("decision", "")
            if dec == "copy":
                s["copies"] += 1
            elif dec in RESOLUTIONS:
                pnl = d.get("realized_pnl_override")
                if pnl is not None:
                    s["games"][game_key_for_slug(d.get("slug", ""))] += float(pnl)
            elif "sell" in dec:
                pnl = d.get("realized_pnl_override")
                if pnl is not None:
                    s["sell_pnl"] += float(pnl)
                    s["sell_n"] += 1

    results = []
    for name, entry in tracked.items():
        s = stats.get(name)
        added = entry.get("added_date")
        try:
            added_ts = datetime.fromisoformat(str(added).replace("Z", "+00:00")).timestamp() if added else None
        except Exception:
            added_ts = None
        r = {"name": name, "address": entry.get("address", ""),
             "notes": (entry.get("notes") or "")[:160],
             "tracked_days": round((now - added_ts) / 86400, 1) if added_ts else 999.0}
        if not s:
            r.update(games=0, wins=0, losses=0, wr=0.0, res_pnl=0.0,
                     avg_game=0.0, sell_pnl=0.0, copies=0,
                     span_days=0.0, silent_days=999.0, flags=["NO_DATA_30D"])
            r["verdict"], r["reason"] = _verdict(r)
            results.append(r)
            continue
        g = s["games"]
        wins = sum(1 for v in g.values() if v > 0)
        losses = sum(1 for v in g.values() if v < 0)
        res_pnl = sum(g.values())
        n = len(g)
        span = (s["last"] - s["first"]) / 86400 if s["first"] else 0.0
        silent = (now - s["last"]) / 86400 if s["last"] else 999.0
        cpd = s["copies"] / span if span > 0.2 else 0.0
        flags = []
        if cpd > HFT_COPIES_PER_DAY:
            flags.append("HFT")
        if silent > DORMANT_DAYS:
            flags.append("DORMANT")
        if abs(s["sell_pnl"]) > 0.01:
            flags.append("HAS_SELL_PNL")
        r.update(games=n, wins=wins, losses=losses,
                 wr=round(wins / (wins + losses) * 100, 1) if wins + losses else 0.0,
                 res_pnl=round(res_pnl, 2),
                 avg_game=round(res_pnl / n, 2) if n else 0.0,
                 sell_pnl=round(s["sell_pnl"], 2), copies=s["copies"],
                 span_days=round(span, 1), silent_days=round(silent, 1),
                 flags=flags)
        r["verdict"], r["reason"] = _verdict(r)
        results.append(r)

    order = {"PROMOTE_CANDIDATE": 0, "WATCH": 1, "TOO_EARLY": 2,
             "HOLD": 3, "DEMOTE_CANDIDATE": 4}
    results.sort(key=lambda x: (order.get(x["verdict"], 9), -x["res_pnl"]))
    return {"scanned_at": now,
            "scanned_at_iso": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "window_days": WINDOW_DAYS,
            "denomination_note": "Shadow P&L at $100/copy base (~10x live sizing)",
            "wallets": results}


def _verdict(r):
    """(verdict, human reason). Order of checks matters."""
    if "NO_DATA_30D" in r["flags"]:
        if r.get("tracked_days", 999) < NEW_WALLET_GRACE_DAYS:
            return "TOO_EARLY", f"tracked only {r['tracked_days']:.0f}d — accumulating"
        return "DEMOTE_CANDIDATE", "zero rows in 30d window — dead tracking"
    if "DORMANT" in r["flags"]:
        return "DEMOTE_CANDIDATE", f"no activity {r['silent_days']:.0f}d (>{DORMANT_DAYS}d)"
    if r["games"] >= WATCH_MIN_GAMES and r["wr"] >= HARVESTER_WR and r["avg_game"] < HARVESTER_MIN_AVG:
        return "HOLD", (f"harvester profile: {r['wr']}% WR at ${r['avg_game']}/game — "
                        "loss tail unobserved, likely uncopyable at flat sizing")
    if r["games"] >= DEMOTE_MIN_GAMES and r["res_pnl"] < 0:
        return "DEMOTE_CANDIDATE", f"negative (${r['res_pnl']}) at {r['games']} games"
    if r["games"] >= PROMOTE_MIN_GAMES and r["wr"] >= PROMOTE_MIN_WR \
            and r["res_pnl"] > 0 and r["avg_game"] > 0:
        return "PROMOTE_CANDIDATE", (f"{r['games']} games, {r['wr']}% WR, "
                                     f"${r['avg_game']}/game — run entry-price killshot before promoting")
    if r["games"] >= PROMOTE_MIN_GAMES and r["res_pnl"] > 0:
        return "HOLD", f"sample OK but WR {r['wr']}% or economics thin (${r['avg_game']}/game)"
    if r["games"] >= WATCH_MIN_GAMES:
        if r["res_pnl"] > 0:
            return "WATCH", f"{r['games']} games, positive, needs {PROMOTE_MIN_GAMES}+"
        return "WATCH", f"{r['games']} games, negative — demote-track if still red at {DEMOTE_MIN_GAMES}"
    return "TOO_EARLY", f"only {r['games']} games resolved"


def scan_and_cache():
    result = scan()
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=1))
    tmp.replace(CACHE_PATH)
    return result


if __name__ == "__main__":
    res = scan_and_cache()
    print(f"scanned {len(res['wallets'])} wallets at {res['scanned_at_iso']}")
    print(f"({res['denomination_note']})\n")
    for w in res["wallets"]:
        fl = " [" + ",".join(w["flags"]) + "]" if w["flags"] else ""
        print(f"{w['verdict']:18s} {w['name']:22s} {w['games']:3d}g "
              f"{w['wr']:5.1f}% ${w['res_pnl']:>8.2f} (${w['avg_game']}/g){fl}")
        print(f"{'':18s}   -> {w['reason']}")
