"""Parameter Optimizer — per-wallet + portfolio recommendations.

Per-wallet: bet-size/capital feasibility (from concurrent-position
history), capture recovery (from skip logs), stop-loss verdict (species-
routed; longshot books get 'sizing is your only lever' per the Jul 10
all-negative sweep). Portfolio: category-correlation of daily P&L,
shared-capital allocation notes.

HONESTY RULES (enforced, not aspirational):
- n < 25 distinct games -> INSUFFICIENT_DATA, no recommendations
- fill quality at size is UNKNOWABLE on paper -> every sizing rec
  carries the caveat
- recommendations only; Apply is a separate human-confirmed step
"""
import json
import pathlib
import sys
from datetime import datetime, timezone
from collections import defaultdict, Counter

BOT = pathlib.Path("/opt/polymarket-bot")
sys.path.insert(0, str(BOT / "fundamentals"))
from outcome_tracker import game_key_for_slug  # noqa: E402

PT = BOT / "logs" / "paper_trades.jsonl"
HISTORY = pathlib.Path("/opt/dashview/config_history.json")
SPECIES = pathlib.Path("/opt/dashview/wallet_species.json")
CACHE = pathlib.Path("/opt/dashview/optimizer_cache.json")

CLOSES = {"resolved", "stop_loss_triggered", "stop_loss_live", "profit_take_triggered"}
MIN_GAMES = 25


def _config_start(name, hist):
    ch = hist.get(name) or []
    if not ch:
        return None
    return datetime.fromisoformat(max(c["date"] for c in ch)).replace(
        tzinfo=timezone.utc).timestamp()


def _load(now):
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    species = json.loads(SPECIES.read_text()) if SPECIES.exists() else {}
    names = [k for k in hist if not k.startswith("_")]
    import yaml
    roster = yaml.safe_load(open(BOT / "config" / "roster.yaml"))
    sizing = {e["name"]: e.get("sizing", {}) for e in roster if e.get("name") in names}

    per = {n: {"games": defaultdict(float), "dec": Counter(), "opens": {},
               "concurrent": [], "daily": defaultdict(float),
               "start": _config_start(n, hist)} for n in names}
    with open(PT) as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            tr = d.get("trader")
            if tr not in per:
                continue
            s = per[tr]
            ts = float(d.get("timestamp") or 0)
            if s["start"] and ts < s["start"]:
                continue
            dec = d.get("decision", "")
            cid = d.get("condition_id") or ""
            if dec == "copy":
                s["dec"]["copy"] += 1
                if cid:
                    s["opens"][cid] = ts
                    s["concurrent"].append((ts, +1, cid))
            elif dec.startswith("skip"):
                s["dec"][dec] += 1
            elif dec in CLOSES:
                pnl = d.get("realized_pnl_override")
                if pnl is not None:
                    s["games"][game_key_for_slug(d.get("slug", ""))] += float(pnl)
                    s["daily"][int(ts // 86400)] += float(pnl)
                if cid in s["opens"]:
                    s["concurrent"].append((ts, -1, cid))
                    del s["opens"][cid]
    return names, hist, species, sizing, per


def _peak_concurrent(events, expiry_hours=10.0):
    """Peak CAPITAL-WEIGHTED overlap. A cid is ONE position: interval =
    first open -> close event (scale-ins extend weight, not count).
    Weight per position = min(entries, 2) base-units (halving-decay
    converges to ~2x base committed per market). Orphans (no close)
    expire at expiry_hours. Returns peak in base-units — multiply by
    base_usd for committed dollars."""
    first_open = {}
    entries = {}
    close_at = {}
    for ts, delta, cid in sorted(events):
        if delta > 0:
            first_open.setdefault(cid, ts)
            entries[cid] = entries.get(cid, 0) + 1
        else:
            close_at.setdefault(cid, ts)
    pts = []
    for cid, start in first_open.items():
        w = min(entries.get(cid, 1), 2)
        end = close_at.get(cid, start + expiry_hours * 3600)
        if end <= start:
            end = start + 600
        pts.append((start, +w))
        pts.append((end, -w))
    cur = peak = 0.0
    for _, d in sorted(pts):
        cur += d
        peak = max(peak, cur)
    return peak


def analyze(now=None):
    now = now or datetime.now(timezone.utc).timestamp()
    names, hist, species_map, sizing, per = _load(now)
    wallets = []

    for name in names:
        s = per[name]
        sp = (species_map.get(name) or {}).get("species", "grinder")
        sz = sizing.get(name, {})
        base = float(sz.get("base_usd") or 0)
        capital = float(sz.get("starting_capital") or 600)
        net = sum(s["games"].values())
        n = len(s["games"])
        recs = []
        evidence = {}

        if n < MIN_GAMES:
            wallets.append({"name": name, "species": sp,
                            "verdict": "INSUFFICIENT_DATA",
                            "note": f"{n} distinct games on current config (need {MIN_GAMES})",
                            "recs": [], "evidence": {}})
            continue

        # --- capital feasibility ---
        expiry = {"longshot_firehose": 10.0, "grinder": 12.0,
                  "longhold": 96.0}.get(sp, 24.0)
        peak = _peak_concurrent(s["concurrent"], expiry_hours=expiry)
        committed_at_peak = peak * base  # upper bound: every slot at full base
        headroom = capital + net - committed_at_peak
        evidence["peak_committed_base_units"] = round(peak, 1)
        evidence["est_committed_at_peak"] = round(committed_at_peak, 2)
        tot_dec = sum(s["dec"].values())
        cap_skips = sum(v for k, v in s["dec"].items() if "capital" in k)
        cap_pct = cap_skips / tot_dec * 100 if tot_dec else 0
        min_skips = s["dec"].get("skip_min_size", 0)
        evidence["capital_skip_pct"] = round(cap_pct, 1)
        evidence["min_size_skips"] = min_skips

        if cap_pct > 2:
            max_afford = (capital + net) / peak if peak else base
            evidence["peak_utilization_pct"] = round(base / max_afford * 100, 0) if max_afford else None
            if max_afford < base * 0.95:
                recs.append({
                    "param": "base_usd", "current": base,
                    "suggest": round(max_afford, 2),
                    "why": f"{cap_pct:.0f}% capital skips; peak {peak:.0f} base-units x ${base} exceeds pool. Max affordable ~${max_afford:.2f}/bet.",
                    "caveat": "paper fills at mid — real fill quality at any size untested"})
            else:
                evidence["capital_note"] = f"{cap_pct:.0f}% capital skips = brief peak saturation at {base/max_afford*100:.0f}% utilization — correctly sized, not starved"
        elif min_skips > tot_dec * 0.05 and cap_pct < 1 and net > 0:
            # economics gate: never recommend capturing MORE of a wallet
            # that's losing on current config (e.g. tripwired Sportmasters)
            grow = round(base * 1.2, 2)
            afford = (capital + net) / peak if peak else 1e9
            if grow < afford:
                recs.append({
                    "param": "base_usd", "current": base, "suggest": grow,
                    "why": f"{min_skips} min-size skips, capital idle at peak (headroom ${headroom:.0f}) — modest raise recovers decayed re-entries.",
                    "caveat": "paper fills at mid — real fill quality untested"})

        # --- stop-loss verdict (species-routed) ---
        if sp.startswith("longshot"):
            recs.append({"param": "stop_loss", "current": "watchdog-only",
                         "suggest": "no change",
                         "why": "longshot species: Jul 10 counterfactual sweep NET-NEGATIVE at every age/threshold cell — winners and losers live identical lifetimes. Sizing is the only drawdown lever.",
                         "caveat": None})
        # --- deviation / capture (informational) ---
        dev_skips = sum(v for k, v in s["dec"].items() if "deviation" in k)
        evidence["deviation_skip_pct"] = round(dev_skips / tot_dec * 100, 1) if tot_dec else 0

        wallets.append({"name": name, "species": sp, "verdict": "ANALYZED",
                        "recs": recs, "evidence": evidence,
                        "stats": {"games": n, "net": round(net, 2),
                                  "base_usd": base, "capital": capital}})

    # --- portfolio layer: daily P&L correlation across wallets ---
    days = sorted({d for s in per.values() for d in s["daily"]})
    matrix = {}
    analyzed = [w["name"] for w in wallets if w["verdict"] == "ANALYZED"]
    for i, a in enumerate(analyzed):
        for b in analyzed[i + 1:]:
            xs = [per[a]["daily"].get(d, 0.0) for d in days]
            ys = [per[b]["daily"].get(d, 0.0) for d in days]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            vx = sum((x - mx) ** 2 for x in xs) ** 0.5
            vy = sum((y - my) ** 2 for y in ys) ** 0.5
            corr = cov / (vx * vy) if vx and vy else 0
            matrix[f"{a}|{b}"] = round(corr, 2)
    high_corr = {k: v for k, v in matrix.items() if v > 0.5}
    portfolio_notes = []
    if high_corr:
        portfolio_notes.append(
            f"correlated daily P&L (>0.5): {high_corr} — these wallets draw down together; shared-capital exposure compounds on bad slates")
    if len(days) < 10:
        portfolio_notes.append(
            f"correlation over only {len(days)} shared days — directional at best")

    out = {"analyzed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
           "wallets": wallets, "portfolio": {"daily_pnl_correlation": matrix,
                                             "notes": portfolio_notes}}
    CACHE.write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    res = analyze()
    for w in res["wallets"]:
        print(f"\n{w['verdict']:18s} {w['name']} [{w['species']}]")
        if w["verdict"] == "INSUFFICIENT_DATA":
            print(f"  {w['note']}")
            continue
        for k, v in w["evidence"].items():
            print(f"  {k}: {v}")
        for r in w["recs"]:
            print(f"  → {r['param']}: {r['current']} -> {r['suggest']}")
            print(f"    why: {r['why']}")
            if r.get("caveat"):
                print(f"    ⚠ {r['caveat']}")
    print("\nPORTFOLIO:")
    for k, v in res["portfolio"]["daily_pnl_correlation"].items():
        print(f"  corr {k}: {v}")
    for note in res["portfolio"]["notes"]:
        print(f"  ⚠ {note}")
