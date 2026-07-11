"""Daily scanner refresh + Telegram alert on verdict CHANGES only.
Cron: quiet-hour daily. Compares fresh scan against previous cache;
steady states stay silent, transitions ping."""
import json, sys, pathlib

sys.path.insert(0, "/opt/dashview")
import scanner

ALERT_WORTHY = {
    ("*", "PROMOTE_CANDIDATE"): "🚀 {name} is now a PROMOTE candidate: {reason}",
    ("*", "DEMOTE_CANDIDATE"): "📉 {name} flagged for demotion: {reason}",
    ("PROMOTE_CANDIDATE", "*"): "↩️ {name} dropped out of promote candidacy: {reason}",
}

def _match(prev, new):
    for (p, n), msg in ALERT_WORTHY.items():
        if (p == "*" or p == prev) and (n == "*" or n == new) and prev != new:
            return msg
    return None

def main():
    prev = {}
    if scanner.CACHE_PATH.exists():
        try:
            old = json.loads(scanner.CACHE_PATH.read_text())
            prev = {w["name"]: w["verdict"] for w in old.get("wallets", [])}
        except Exception:
            pass

    result = scanner.scan_and_cache()
    changes = []
    for w in result["wallets"]:
        old_v = prev.get(w["name"])
        if old_v is None:
            continue   # new wallet, no transition to report
        msg = _match(old_v, w["verdict"])
        if msg:
            changes.append(msg.format(name=w["name"], reason=w["reason"]))

    print(f"scanned {len(result['wallets'])} wallets; {len(changes)} verdict changes")
    if changes:
        text = "🔬 Shadow Scanner:\n" + "\n".join(changes)
        sent = False
        try:
            from server import send_push_notification
            send_push_notification("🔬 Shadow Scanner", "\n".join(changes), tag="scanner_verdict")
            sent = True
            print("web-push sent")
        except Exception as e:
            print(f"web-push failed ({e})")
        try:
            sys.path.insert(0, "/opt/polymarket-bot")
            from src import alerts
            alerts.emit("scanner_verdict", {"message": text, "changes": changes})
        except Exception:
            pass
        if not sent:
            print("changes (logged only):")
            for c in changes:
                print("  " + c)

if __name__ == "__main__":
    main()
