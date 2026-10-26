"""Maple Flip Hunter — Friends Edition licence server (reference implementation, standard library + cryptography).

Run on your own machine/VPS (put it behind HTTPS, e.g. Caddy/nginx):
    python server/license_server.py --init            # creates keys + database once (prints the PUBLIC key)
    python server/license_server.py --serve 8787      # start
    python server/license_server.py --code 30         # new activation code worth 30 days (0 = lifetime)
    python server/license_server.py --list            # machines, trial start, status
    python server/license_server.py --update 0.8.1 https://your.host/MapleFlipHunterFriends.exe path\\to\\that.exe
                                                      # publish a signed update manifest

Endpoints (JSON):  POST /v1/activate {machine, version}  POST /v1/status {machine, version}
                   POST /v1/redeem {machine, code, version}   GET /v1/update
Trial = 72 h from the FIRST activation of a machine id, by this server's clock. The private key never leaves
this folder; the client only gets the public key (server/public_key.txt → friends_config.json)."""
from __future__ import annotations

import argparse
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
    if licensed_until is not None and (licensed_until == 0 or licensed_until > now):
        st, exp = "licensed", (None if licensed_until == 0 else float(licensed_until))
    else:
        trial_end = first + TRIAL_SECONDS
        st, exp = ("trial", trial_end) if now < trial_end else ("expired", trial_end)
    payload = {"m": machine, "st": st, "exp": exp, "iat": now, "ver": 1}
    return {"token": sign(payload, key), "server_time": now, "status": st, "expires_at": exp}


def redeem(c: sqlite3.Connection, machine: str, code: str) -> tuple[bool, str]:
    row = c.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    if row is None:
        return False, "unknown activation code"
    if row["redeemed_by"] and row["redeemed_by"] != machine:
        return False, "this code was already used on another computer"
    now = time.time()
    until = 0 if int(row["days"]) == 0 else now + int(row["days"]) * 86400
    c.execute("INSERT INTO machines(machine, first_seen, last_seen, version, licensed_until) VALUES (?,?,?,?,?) "
              "ON CONFLICT(machine) DO UPDATE SET licensed_until=excluded.licensed_until, last_seen=excluded.last_seen",
              (machine, now, now, "", until))
    c.execute("UPDATE codes SET redeemed_by=?, redeemed_at=? WHERE code=?", (machine, now, code))
    c.commit()
    return True, "ok"


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
        n = int(self.headers.get("Content-Length") or 0)
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


def serve(port: int) -> ThreadingHTTPServer:
    Handler.key = load_key()
    srv = ThreadingHTTPServer(("0.0.0.0" if port else "127.0.0.1", port), Handler)
    return srv


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
    a = ap.parse_args()
    if a.init:
        init()
    elif a.code is not None:
        print("activation code:", make_code(a.code))
    elif a.list:
        for r in db().execute("SELECT * FROM machines ORDER BY first_seen"):
            print(r["machine"][:12], time.strftime("%Y-%m-%d %H:%M", time.localtime(r["first_seen"])),
                  "licensed_until", r["licensed_until"], r["version"])
    elif a.update:
        print(json.dumps(publish_update(*a.update), indent=2))
    elif a.serve is not None:
        print(f"licence server on port {a.serve} (public key {PUB_FILE.read_text().strip()[:16]}…)")
        serve(a.serve).serve_forever()
    else:
        ap.print_help()
