"""Read tv_snapshots.jsonl -> per-wallet daily high/low/amplitude/current.
Usage: tv_report.py [wallet] [days]"""
import json
import sys
from datetime import datetime, timezone
from collections import defaultdict

wallet_filter = sys.argv[1] if len(sys.argv) > 1 else None
days = int(sys.argv[2]) if len(sys.argv) > 2 else 7

by = defaultdict(lambda: defaultdict(list))  # wallet -> day -> [tv...]
for ln in open("/opt/dashview/tv_snapshots.jsonl"):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    if wallet_filter and d["wallet"] != wallet_filter:
        continue
    day = datetime.fromtimestamp(d["ts"], timezone.utc).strftime("%m-%d")
    by[d["wallet"]][day].append((d["ts"], d["tv"]))

for w in sorted(by):
    print(f"\n{w}:")
    for day in sorted(by[w])[-days:]:
        pts = sorted(by[w][day])
        tvs = [t for _, t in pts]
        hi, lo = max(tvs), min(tvs)
        hi_t = datetime.fromtimestamp(pts[tvs.index(hi)][0], timezone.utc).strftime("%H:%M")
        lo_t = datetime.fromtimestamp(pts[tvs.index(lo)][0], timezone.utc).strftime("%H:%M")
        print(f"  {day}: hi ${hi:,.0f} ({hi_t}) | lo ${lo:,.0f} ({lo_t}) | amplitude ${hi-lo:,.0f} | close ${tvs[-1]:,.0f} | {len(pts)} pts")
