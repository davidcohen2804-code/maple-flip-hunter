"""Gate for the EXE smoke run: the packaged binary must have ingested real rows from at least one source."""
import json
import sys

path = sys.argv[1]
res = json.load(open(path, encoding="utf-8"))
m = res["metrics"]
online = [h["name"] for h in res["health"] if h["status"] in ("ONLINE", "STALE")]
print(f"scanner_state={res['scanner_state']} online={online} ingested={m['listings_ingested']} "
      f"new={m['listings_new']} evaluations={m['evaluations']} alerts={m['alerts']} "
      f"source_age_avg={m.get('source_age_at_ingest_avg_s')} analysis_ms_avg={m.get('analysis_ms_avg')}")
if not online or m["listings_ingested"] <= 0 or res["scanner_state"] not in ("LIVE", "DEGRADED"):
    print("GATE FAILED: packaged EXE did not receive live market data")
    sys.exit(1)
print("GATE PASSED")
