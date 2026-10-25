"""Local Club FM simulator — lets the real EXE exercise the Club FM adapter end-to-end at home WITHOUT touching
maplefm.gg. Serves /data/snap (public) and /data/live (needs 'Authorization: Bearer demo-token') in Club FM's
columnar v2 format, and injects one obviously under-priced listing every ~90 s so an alert fires.

Run:   python tools/clubfm_simulator.py            (listens on http://127.0.0.1:8765)
Then in %APPDATA%\\MapleFlipHunter\\settings.json set
   "source_clubfm_base_url": "http://127.0.0.1:8765", "source_clubfm_permission_granted": true,
   "source_clubfm_token": "demo-token"     (leave the token empty to test snapshot mode / the back-off)
and start the app. Remove the base_url line to go back to the real server.
"""
from __future__ import annotations

import json
import random
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

ITEMS = ["Hat Accuracy Scroll: Intermediate", "Cape STR Scroll: Lesser", "Earring INT Scroll: Intermediate",
         "Pan Lid", "Ice Wand", "Rainbow Knit"]
REF = {n: 40_000 + 7_000 * i for i, n in enumerate(ITEMS)}
TOKEN = "demo-token"
START = time.time()


def snapshot(after_ms: int = 0) -> dict:
    now = time.time()
    T = int(now * 1000) - 5_000
    rows = []
    for k in range(240):                         # 40 per item: 30 completed sales + 10 live asks
        name = ITEMS[k % len(ITEMS)]
        sold = (k // len(ITEMS)) < 30
        price = int(REF[name] * random.uniform(0.95, 1.08))
        rows.append((name, f"shop{k % 37}", price, "FM%d" % (1 + k % 6), 1 + k % 20, random.randint(60, 3_600), sold))
    cycle = int((now - START) // 90)
    rows.append((ITEMS[cycle % len(ITEMS)], "NoobTrader", int(REF[ITEMS[cycle % len(ITEMS)]] * 0.18), "FM3", 7, 20, False))
    rows = [r for r in rows if after_ms == 0 or (T - r[5] * 1000) > after_ms]
    words_i, words_s, words_r = list(dict.fromkeys(r[0] for r in rows)), list(dict.fromkeys(r[1] for r in rows)), list(dict.fromkeys(r[3] for r in rows))
    cols = {"i": [words_i.index(r[0]) for r in rows], "s": [words_s.index(r[1]) for r in rows],
            "p": [r[2] for r in rows], "u": [r[2] for r in rows], "q": [1] * len(rows),
            "r": [words_r.index(r[3]) for r in rows], "c": [r[4] for r in rows], "t": [r[5] for r in rows],
            "st": [0 if r[6] else 1 for r in rows], "sold": [1 if r[6] else 0 for r in rows]}
    return {"v": 2, "at": int(now * 1000), "T": T, "n": len(rows), "cols": cols,
            "words": {"i": words_i, "s": words_s, "r": words_r, "st": ["old", "up"]},
            "kinds": {"i": "w", "s": "w", "p": "v", "u": "v", "q": "v", "r": "w", "c": "v", "t": "t", "st": "w", "sold": "v"},
            "D": {"active": [{"@": i} for i, r in enumerate(rows) if not r[6]],
                  "sales": [{"@": i} for i, r in enumerate(rows) if r[6]]}}


class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/data/snap":
            return self._json(snapshot())
        if u.path == "/data/live":
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._json({"error": "unauthorized"}, 401)
            after = int((parse_qs(u.query).get("after") or ["0"])[0])
            return self._json(snapshot(after))
        self._json({"error": "not found"}, 404)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "max-age=30")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: D401
        print(time.strftime("%H:%M:%S"), self.headers.get("User-Agent", "?")[:40], fmt % args)


if __name__ == "__main__":
    print("Club FM simulator on http://127.0.0.1:8765  (token: demo-token). Ctrl+C to stop.")
    HTTPServer(("127.0.0.1", 8765), H).serve_forever()
