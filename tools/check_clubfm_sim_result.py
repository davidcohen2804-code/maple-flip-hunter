"""Gate for the EXE run against the local Club FM simulator: the packaged binary must poll the simulator in LIVE
mode (token), ingest its rows, raise at least one alert on the injected under-priced listing, and record the
ingest->sound latency. No maplefm.gg traffic is involved."""
import json
import sys

res = json.load(open(sys.argv[1], encoding="utf-8"))
m, h = res["metrics"], {x["name"]: x for x in res["health"]}
cf = h.get("clubfm")
print(f"state={res['scanner_state']} clubfm={cf and cf['status']} detail={cf and cf.get('detail')!r} ingested={m['listings_ingested']} "
      f"alerts={m['alerts']} sound_latency_ms={res.get('sound_latency_ms')}")
ok = (cf and cf["status"] == "ONLINE" and "live" in (cf.get("detail") or "") and m["listings_ingested"] > 0
      and m["alerts"] >= 1 and res.get("sound_latency_ms"))
if not ok:
    print("GATE FAILED: Club FM simulated chain (live feed -> ingest -> alert -> sound) did not complete")
    sys.exit(1)
print(f"GATE PASSED: Club FM live feed -> detection -> sound in the packaged EXE; alerts={res['alerts'][:2]}")
