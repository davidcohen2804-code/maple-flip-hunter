"""Manage the Cloudflare licence server (server/cloudflare/worker.js) from your PC. No secrets in the repo:

    set MFH_SERVER=https://mfh-license.<your-subdomain>.workers.dev      (once per terminal, or put both in
    set MFH_ADMIN_TOKEN=<the ADMIN_TOKEN you chose>                       server\\cf_admin.json — git-ignored)

    python server\\cf_admin.py code 14            one-time 14-day activation code
    python server\\cf_admin.py code 0             one-time LIFETIME code
    python server\\cf_admin.py list               every machine: first seen, last seen, version, status; unused codes
    python server\\cf_admin.py check 3fa9c1       one machine (prefix shown on the friend's lock screen)
    python server\\cf_admin.py revoke 3fa9c1      lock that machine (within 10 min); unrevoke = back to trial rules
    python server\\cf_admin.py unrevoke 3fa9c1
    python server\\cf_admin.py usage              requests per day vs the free quota (approximate) + machine count
    python server\\cf_admin.py update 0.12.1 https://github.com/<you>/MapleFlipHunter/releases/download/v0.12.1/MapleFlipHunterFriends.exe dist\\MapleFlipHunterFriends.exe
    python server\\cf_admin.py health
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
CFG = HERE / "cf_admin.json"


def _cfg() -> tuple[str, str]:
    data = json.loads(CFG.read_text(encoding="utf-8")) if CFG.exists() else {}
    server = (os.environ.get("MFH_SERVER") or data.get("server") or "").rstrip("/")
    token = os.environ.get("MFH_ADMIN_TOKEN") or data.get("admin_token") or ""
    if not server or not token:
        sys.exit("set MFH_SERVER and MFH_ADMIN_TOKEN (or create server/cf_admin.json with server + admin_token)")
    return server, token


def _call(method: str, path: str, body: dict | None = None) -> dict:
    server, token = _cfg()
    r = httpx.request(method, server + path, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    try:
        data = r.json()
    except ValueError:
        data = {"error": r.text[:200]}
    if r.status_code != 200:
        sys.exit(f"{r.status_code}: {data.get('error', data)}")
    return data


def _t(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts))) if ts else "-"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    if cmd == "code":
        days = int(args[0]) if args else 14
        d = _call("POST", "/admin/code", {"days": days})
        print(d["code"], "(lifetime)" if days == 0 else f"({days} days, one computer)")
    elif cmd == "list":
        d = _call("POST", "/admin/list", {})
        print(f"{'machine':14} {'first seen':16} {'last seen':16} {'version':9} status    kind        expires")
        for m in d["machines"]:
            print(f"{m['machine'][:12]:14} {_t(m['first_seen']):16} {_t(m['last_seen']):16} {(m['version'] or '?'):9} "
                  f"{m['status']:9} {m['kind']:11} {_t(m['expires_at'])}")
        unused = [c for c in d["codes"] if not c["redeemed_by"]]
        print(f"\n{len(d['machines'])} machines · {len(d['codes'])} codes ({len(unused)} unused):")
        for c in unused:
            print(f"  {c['code']}  {'lifetime' if c['days'] == 0 else str(c['days']) + ' days'}  created {_t(c['created'])}")
    elif cmd == "check":
        m = _call("POST", "/admin/check", {"machine": args[0]})
        print(json.dumps({**m, "first_seen": _t(m["first_seen"]), "last_seen": _t(m["last_seen"]), "expires_at": _t(m["expires_at"])}, indent=2))
    elif cmd in ("revoke", "unrevoke"):
        print(_call("POST", "/admin/revoke", {"machine": args[0], "revoked": cmd == "revoke"}))
    elif cmd == "usage":
        d = _call("GET", "/admin/usage")
        lim = d["free_limits"]["workers_requests_per_day"]
        print(f"machines: {d['machines']}   free limit: {lim:,} requests/day (approximate counts, batches of 25)")
        for row in d["days"]:
            pct = 100.0 * row["n"] / lim
            flag = "  ⚠ over 80% of the free quota" if pct > 80 else ""
            print(f"  {row['day']}  ~{row['n']:>7,} requests  ({pct:.1f}%){flag}")
        print(d["note"])
    elif cmd == "update":
        version, url, exe = args
        sha = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
        print(json.dumps(_call("POST", "/admin/update", {"version": version, "url": url, "sha256": sha}), indent=2))
    elif cmd == "health":
        server, _ = _cfg()
        print(httpx.get(server + "/v1/health", timeout=20).json())
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
