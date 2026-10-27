"""Gate for the EXE run with an FM Watcher store: own scans must be ingested, valued (reference from the local Club FM
simulator — no live provider is permitted) and the under-priced one must raise an alert — proving scan -> pricing -> alert in the binary."""
import json, sys
res = json.load(open(sys.argv[1], encoding="utf-8"))
h = {x["name"]: x for x in res["health"]}
fm = h.get("fmwatcher")
valued = [v for v in res.get("valued", []) if v["source"] == "fmwatcher"]
priced = [v for v in valued if v.get("reference")]
alerts = [a for a in res.get("alerts", []) if a.get("shop") == "NoobTrader"]
print(f"fmwatcher={fm and fm['status']} detail={fm and fm.get('detail')!r} valued={len(valued)} priced={len(priced)} alerts={alerts}")
if not fm or fm["status"] not in ("ONLINE", "IDLE") or not valued:
    print("GATE FAILED: own scans were not ingested/valued by the packaged EXE"); sys.exit(1)
if not priced:
    print("GATE FAILED: no scanned row received a market reference (Club FM simulator not reachable?)"); sys.exit(1)
if not alerts:
    print("GATE FAILED: the under-priced own scan did not raise an alert"); sys.exit(1)
print("GATE PASSED: scan -> pricing -> alert in the packaged EXE")
