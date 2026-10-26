import json, sys
res = json.load(open(sys.argv[1], encoding="utf-8"))
want = sys.argv[2]
print(f"license status={res['status']} expected={want} remaining_s={res.get('remaining_s')} online={res.get('online')} msg={res.get('message')}")
if res["status"] != want:
    print("GATE FAILED: Friends Edition licence state"); sys.exit(1)
if want == "trial" and not (71 * 3600 < (res.get("remaining_s") or 0) <= 72 * 3600):
    print("GATE FAILED: trial length is not 72 h"); sys.exit(1)
print("GATE PASSED")
