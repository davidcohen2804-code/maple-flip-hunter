"""CI helper: redeem an activation code for THIS machine against a local licence server."""
import sys
import httpx
sys.path.insert(0, ".")
from maple_flip_hunter.licensing.machine import machine_id
server, code = sys.argv[1], sys.argv[2].strip()
r = httpx.post(f"{server}/v1/redeem", json={"machine": machine_id(), "code": code, "version": "ci"})
print("redeem", r.status_code, r.json())
sys.exit(0 if r.status_code == 200 and r.json().get("status") == "licensed" else 1)
