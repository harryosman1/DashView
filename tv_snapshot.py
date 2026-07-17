"""Per-wallet total-value snapshots — every N minutes via cron.

Writes rolling JSONL: {ts, wallet, capital, unrealized, tv, open_rows}.
Purpose: intraday MTM history (drawdown anatomy, amplitude measurement,
"what was the high today") — realized P&L is already permanent in
paper_trades.jsonl; marks were previously unrecorded and vanished.
Retention: 14 days (config below), pruned on each run.
"""
import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/polymarket-bot")
from src.pnl_cache import get_cached_pnl  # noqa: E402
import yaml  # noqa: E402

OUT = pathlib.Path("/opt/dashview/tv_snapshots.jsonl")
RETENTION_DAYS = 14

def snap():
    now = datetime.now(timezone.utc).timestamp()
    pnl = get_cached_pnl()
    roster = yaml.safe_load(open("/opt/polymarket-bot/config/roster.yaml"))
    live = {e["name"]: e for e in roster if e.get("tier") == "live"}
    lines = []
    for name, e in live.items():
        rows = [p for p in pnl.open_positions_detail if p.trader == name]
        unreal = sum(getattr(p, "unrealized_pnl", 0) or 0 for p in rows)
        realized = sum(getattr(c, "realized_pnl", 0) or 0 for c in pnl.closed if c.trader == name)
        start = float((e.get("sizing") or {}).get("starting_capital") or 600)
        cap = start + realized
        lines.append(json.dumps({"ts": round(now), "wallet": name,
                                 "capital": round(cap, 2),
                                 "unrealized": round(unreal, 2),
                                 "tv": round(cap + unreal, 2),
                                 "open_rows": len(rows)}))
    # append + prune
    old = []
    if OUT.exists():
        cutoff = now - RETENTION_DAYS * 86400
        for ln in OUT.read_text().splitlines():
            try:
                if json.loads(ln)["ts"] >= cutoff:
                    old.append(ln)
            except Exception:
                continue
    OUT.write_text("\n".join(old + lines) + "\n")
    return len(lines)

if __name__ == "__main__":
    n = snap()
    print(f"snapshotted {n} wallets")
