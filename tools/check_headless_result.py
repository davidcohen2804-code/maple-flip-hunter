"""Gate for the EXE smoke run against the real internet.

Two honest outcomes pass:
  A) at least one PERMITTED source is online and real rows were ingested (scanner LIVE/DEGRADED), or
  B) no source is permitted right now (maplestory.watch Terms 2026-10-27 forbid app access; cozy has no token;
     Club FM permission not recorded): the EXE must then run the whole time, report UNSUPPORTED / AUTH REQUIRED
     with the reason, and make ZERO requests to those sites (no 403 hammering, nothing circumvented).
Anything else — a crash, a blocked source still being polled, a JACKPOT on stale data — fails."""
import json
import sys

path = sys.argv[1]
res = json.load(open(path, encoding="utf-8"))
m = res["metrics"]
health = {h["name"]: h for h in res["health"]}
online = [n for n, h in health.items() if h["status"] in ("ONLINE", "STALE")]
blocked = [n for n, h in health.items() if h["status"] in ("AUTH REQUIRED", "UNAVAILABLE", "UNSUPPORTED")]
print(f"scanner_state={res['scanner_state']} online={online} blocked/unsupported={blocked} ingested={m['listings_ingested']} "
      f"new={m['listings_new']} evaluations={m['evaluations']} alerts={m['alerts']}")
for n, h in health.items():
    print(f"  {n}: {h['status']} requests={h.get('requests')} errors={h.get('errors')} detail={h.get('detail')!r}")

bad = [a for a in res.get("alerts", []) if a.get("level") == "JACKPOT" and a.get("listing_age_s", 0) > 900]
if bad:
    print(f"GATE FAILED: JACKPOT alert on stale data: {bad}")
    sys.exit(1)
if online and m["listings_ingested"] > 0 and res["scanner_state"] in ("LIVE", "DEGRADED"):
    print("GATE PASSED (A): live data from " + ", ".join(online) + (f"; {', '.join(blocked)} not permitted" if blocked else ""))
    sys.exit(0)
# outcome B: nothing permitted → nothing requested
polled_without_permission = [n for n in blocked if (health[n].get("requests") or 0) > 0 or (health[n].get("errors") or 0) > 0]
if online or polled_without_permission or m.get("uptime_s", 0) < res.get("seconds", 0) - 5:
    print(f"GATE FAILED: a source without permission was still polled: {polled_without_permission}" if polled_without_permission
          else "GATE FAILED: packaged EXE did not stay up / inconsistent state")
    sys.exit(1)
if not blocked or "msw" not in health or health["msw"]["status"] != "UNSUPPORTED":
    print("GATE FAILED: maplestory.watch must report UNSUPPORTED (permission required) when not permitted")
    sys.exit(1)
print("GATE PASSED (B): no permitted live source — EXE stayed up, reported why, sent no requests to blocked sites")
