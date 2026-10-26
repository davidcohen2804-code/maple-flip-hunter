"""Maple Flip Hunter — Friends Edition licence server (reference implementation, standard library + cryptography).

Run on your own machine/VPS (put it behind HTTPS, e.g. Caddy/nginx):
    python server/license_server.py --init            # creates keys + database once (prints the PUBLIC key)
    python server/license_server.py --serve 8787      # start
    python server/license_server.py --code 14         # one-time 14-day activation code
    python server/license_server.py --code 0          # one-time LIFETIME activation code
    python server/license_server.py --list            # machines, trial start, status
    python server/license_server.py --update 0.8.1 https://your.host/MapleFlipHunterFriends.exe path\\to\\that.exe
                                                      # publish a signed update manifest
    python server/license_server.py --check <machine-id-prefix>   # one machine's licence state
    python server/license_server.py --revoke <machine-id-prefix>  # lock that machine (status "revoked")
    python server/license_server.py --unrevoke <machine-id-prefix>
    python server/license_server.py --backup                      # copy licenses.sqlite3 to backups/ (keeps 30)
Abuse protection: ≥ 60 requests/min from one IP → HTTP 429 for that IP; bad machine ids rejected.

Endpoints (JSON):  POST /v1/activate {machine, version}  POST /v1/status {machine, version}
                   POST /v1/redeem {machine, code, version}   GET /v1/update
Trial = 72 h from the FIRST activation of a machine id, by this server's clock. The private key never leaves
this folder; the client only gets the public key (server/public_key.txt → friends_config.json)."""
from __future__ import annotations

import argparse
import os
import hashlib
import json
import secrets
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maple_flip_hunter.licensing.tokens import sign  # noqa: E402

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE / "private_key.pem"
PUB_FILE = HERE / "public_key.txt"
DB_FILE = HERE / "licenses.sqlite3"
MANIFEST = HERE / "update_manifest.json"
TRIAL_SECONDS = 72 * 3600


def init() -> None:
    if KEY_FILE.exists():
        print("keys already exist:", KEY_FILE)
    else:
        key = Ed25519PrivateKey.generate()
        KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        PUB_FILE.write_text(pub, encoding="utf-8")
        print("PUBLIC key (put into friends_config.json):", pub)
    db().close()
    print("database:", DB_FILE)


def load_key() -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)  # type: ignore[return-value]


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS machines (machine TEXT PRIMARY KEY, first_seen REAL, last_seen REAL, version TEXT,
                                         licensed_until REAL, note TEXT);
    CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, days INTEGER, created REAL, redeemed_by TEXT, redeemed_at REAL);
    """)
    return c


def token_for(c: sqlite3.Connection, key: Ed25519PrivateKey, machine: str, version: str) -> dict:
    now = time.time()
    row = c.execute("SELECT * FROM machines WHERE machine=?", (machine,)).fetchone()
    if row is None:
        c.execute("INSERT INTO machines(machine, first_seen, last_seen, version, licensed_until) VALUES (?,?,?,?,NULL)",
                  (machine, now, now, version))
        first, licensed_until = now, None
    else:
        c.execute("UPDATE machines SET last_seen=?, version=? WHERE machine=?", (now, version, machine))
        first, licensed_until = float(row["first_seen"]), row["licensed_until"]
    c.commit()
    if licensed_until is not None and licensed_until == -1:
        st, exp, kind = "expired", None, "revoked"             # revoked by the admin: locked until unrevoked
    elif licensed_until is not None and licensed_until == 0:
        st, exp, kind = "licensed", None, "lifetime"
    elif licensed_until is not None and licensed_until > now:
        st, exp, kind = "licensed", float(licensed_until), "activation"
    elif licensed_until is not None:                       # a temporary activation that ran out
        st, exp, kind = "expired", float(licensed_until), "activation"
    else:
        trial_end = first + TRIAL_SECONDS
        st, exp, kind = ("trial", trial_end, "trial") if now < trial_end else ("expired", trial_end, "trial")
    payload = {"m": machine, "st": st, "exp": exp, "iat": now, "kind": kind, "ver": 1}
    return {"token": sign(payload, key), "server_time": now, "status": st, "expires_at": exp, "kind": kind}


def redeem(c: sqlite3.Connection, machine: str, code: str) -> tuple[bool, str]:
    row = c.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    if row is None:
        return False, "unknown activation code"
    if row["redeemed_by"] and row["redeemed_by"] != machine:
        return False, "this code was already used on another computer"
    if row["redeemed_by"] == machine:
        return False, "this code was already used"
    now = time.time()
    until = 0 if int(row["days"]) == 0 else now + int(row["days"]) * 86400
    cur = c.execute("SELECT licensed_until FROM machines WHERE machine=?", (machine,)).fetchone()
    if cur is not None and cur["licensed_until"] == 0 and until != 0:
        return False, "this computer already has a lifetime licence"
    if cur is not None and cur["licensed_until"] == -1:
        return False, "this computer's licence was revoked — contact the developer"
    c.execute("INSERT INTO machines(machine, first_seen, last_seen, version, licensed_until) VALUES (?,?,?,?,?) "
              "ON CONFLICT(machine) DO UPDATE SET licensed_until=excluded.licensed_until, last_seen=excluded.last_seen",
              (machine, now, now, "", until))
    c.execute("UPDATE codes SET redeemed_by=?, redeemed_at=? WHERE code=?", (machine, now, code))
    c.commit()
    return True, "ok"


_hits: dict[str, list[float]] = {}
RATE_PER_MIN = 60


def _rate_limited(ip: str) -> bool:
    now = time.time()
    lst = [t for t in _hits.get(ip, []) if now - t < 60]
    lst.append(now)
    _hits[ip] = lst
    if len(_hits) > 10_000:
        _hits.clear()
    return len(lst) > RATE_PER_MIN


class Handler(BaseHTTPRequestHandler):
    key: Ed25519PrivateKey

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/v1/update"):
            if MANIFEST.exists():
                return self._json(json.loads(MANIFEST.read_text(encoding="utf-8")))
            return self._json({"error": "no update published"}, 404)
        if self.path == "/v1/health":
            return self._json({"ok": True, "server_time": time.time()})
        self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        if _rate_limited(ip):
            return self._json({"error": "too many requests"}, 429)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4096:
            return self._json({"error": "too large"}, 413)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        machine = str(body.get("machine") or "")
        if len(machine) != 64:
            return self._json({"error": "bad machine id"}, 400)
        c = db()
        try:
            if self.path in ("/v1/activate", "/v1/status"):
                return self._json(token_for(c, self.key, machine, str(body.get("version") or "")))
            if self.path == "/v1/redeem":
                ok, msg = redeem(c, machine, str(body.get("code") or "").strip().upper())
                if not ok:
                    return self._json({"error": msg}, 403)
                return self._json(token_for(c, self.key, machine, str(body.get("version") or "")))
        finally:
            c.close()
        self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # noqa: D401
        print(time.strftime("%H:%M:%S"), self.client_address[0], fmt % args)


def serve(port: int, bind: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Bind to localhost by default: put Caddy/nginx (HTTPS) in front. The private key never leaves this folder."""
    Handler.key = load_key()
    srv = ThreadingHTTPServer((bind, port), Handler)
    import threading

    def backups() -> None:
        while True:
            time.sleep(3600)
            try:
                backup()
            except Exception as exc:  # noqa: BLE001
                print("backup failed:", exc)
    threading.Thread(target=backups, daemon=True).start()
    return srv


def find_machine(prefix: str) -> str:
    c = db()
    rows = c.execute("SELECT machine FROM machines WHERE machine LIKE ?", (prefix + "%",)).fetchall()
    c.close()
    if len(rows) != 1:
        raise SystemExit(f"{len(rows)} machines match {prefix!r} — give more characters")
    return rows[0]["machine"]


def set_revoked(prefix: str, revoked: bool) -> None:
    m = find_machine(prefix)
    c = db()
    c.execute("UPDATE machines SET licensed_until=? WHERE machine=?", (-1 if revoked else None, m))
    c.commit(); c.close()
    print(("revoked" if revoked else "unrevoked (back to trial rules)"), m[:12])


def describe(prefix: str) -> None:
    m = find_machine(prefix)
    c = db()
    r = c.execute("SELECT * FROM machines WHERE machine=?", (m,)).fetchone()
    tok = token_for(c, load_key(), m, r["version"] or "")
    c.close()
    print(f"machine {m[:12]}… first_seen {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['first_seen']))} "
          f"last_seen {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['last_seen']))} version {r['version']} "
          f"-> {tok['status']} ({tok['kind']}) expires {time.strftime('%Y-%m-%d %H:%M', time.localtime(tok['expires_at'])) if tok['expires_at'] else '-'}")


def backup() -> Path:
    import shutil

    d = HERE / "backups"
    d.mkdir(exist_ok=True)
    dst = d / f"licenses-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    shutil.copy2(DB_FILE, dst)
    old = sorted(d.glob("licenses-*.sqlite3"))[:-30]
    for p in old:
        p.unlink()
    return dst


def make_code(days: int) -> str:
    code = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    c = db()
    c.execute("INSERT INTO codes(code, days, created) VALUES (?,?,?)", (code, days, time.time()))
    c.commit()
    c.close()
    return code


def publish_update(version: str, url: str, exe_path: str) -> dict:
    sha = hashlib.sha256(Path(exe_path).read_bytes()).hexdigest()
    payload = {"version": version, "url": url, "sha256": sha, "published": time.time()}
    manifest = {**payload, "signature": sign(payload, load_key()).split(".", 1)[1]}
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--serve", type=int, metavar="PORT")
    ap.add_argument("--code", type=int, metavar="DAYS", help="new activation code (0 = lifetime)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--update", nargs=3, metavar=("VERSION", "URL", "EXE"))
    ap.add_argument("--check", metavar="MACHINE")
    ap.add_argument("--revoke", metavar="MACHINE")
    ap.add_argument("--unrevoke", metavar="MACHINE")
    ap.add_argument("--backup", action="store_true")
    a = ap.parse_args()
    if a.init:
        init()
    elif a.code is not None:
        print("activation code:", make_code(a.code))
    elif a.list:
        c = db(); key = load_key()
        for r in c.execute("SELECT * FROM machines ORDER BY first_seen").fetchall():
            tok = token_for(c, key, r["machine"], r["version"] or "")
            print(f"{r['machine'][:12]}  first {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['first_seen']))}  "
                  f"last {time.strftime('%m-%d %H:%M', time.localtime(r['last_seen']))}  v{r['version'] or '?':8s} "
                  f"{tok['status']:9s} {tok['kind']}")
    elif a.update:
        print(json.dumps(publish_update(*a.update), indent=2))
    elif a.check:
        describe(a.check)
    elif a.revoke:
        set_revoked(a.revoke, True)
    elif a.unrevoke:
        set_revoked(a.unrevoke, False)
    elif a.backup:
        print("backup:", backup())
    elif a.serve is not None:
        bind = os.environ.get("MFH_BIND", "127.0.0.1")
        print(f"licence server on {bind}:{a.serve} (public key {PUB_FILE.read_text().strip()[:16]}…) — hourly backups to backups/")
        serve(a.serve, bind).serve_forever()
    else:
        ap.print_help()
