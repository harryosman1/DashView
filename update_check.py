"""Self-update awareness: compare local git HEAD against the GitHub repo.
Pushes a notification (via the update_available toggle) when the repo
has commits this install doesn't. Pull-based — each operator's server
checks GitHub itself; nobody can push to anyone else's devices.
Quiet-failure by design: GitHub unreachable / not a git dir -> skip."""
import json, subprocess, sys, pathlib

REPO_API = "https://api.github.com/repos/harryosman1/DashView/commits/main"
DASHVIEW = pathlib.Path("/opt/dashview")
STATE = DASHVIEW / ".update_check_state.json"


def local_head():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=DASHVIEW,
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def remote_head():
    try:
        import httpx
        r = httpx.get(REPO_API, timeout=15,
                      headers={"Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None, None
        d = r.json()
        msg = (d.get("commit", {}).get("message") or "").split("\n")[0][:80]
        return d.get("sha"), msg
    except Exception:
        return None, None


def main():
    local = local_head()
    if not local:
        print("not a git checkout — skip")
        return
    remote, msg = remote_head()
    if not remote:
        print("github unreachable — skip")
        return
    if remote == local:
        print("up to date")
        return
    # don't re-notify for the same remote sha
    try:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
    except Exception:
        state = {}
    if state.get("notified_sha") == remote:
        print("update pending, already notified")
        return
    try:
        sys.path.insert(0, str(DASHVIEW))
        from server import send_push_notification
        send_push_notification("⬆️ DashView update available",
                               f"New: {msg}" if msg else "New commits on GitHub",
                               tag="update_available")
        print(f"notified: {remote[:8]} ({msg})")
        STATE.write_text(json.dumps({"notified_sha": remote}))
    except Exception as e:
        print(f"push failed ({e}) — will retry next run")


if __name__ == "__main__":
    main()
