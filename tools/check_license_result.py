import json, sys
res = json.load(open(sys.argv[1], encoding="utf-8"))
want = sys.argv[2]
want_kind = sys.argv[3] if len(sys.argv) > 3 else None
print(f"license status={res['status']} kind={res.get('kind')} expected={want}/{want_kind} remaining_s={res.get('remaining_s')} msg={res.get('message')}")
if res["status"] != want or (want_kind and res.get("kind") != want_kind):
    print("GATE FAILED: Friends Edition licence state"); sys.exit(1)
if want == "trial" and not (71 * 3600 < (res.get("remaining_s") or 0) <= 72 * 3600):
    print("GATE FAILED: trial length is not 72 h"); sys.exit(1)
if want_kind == "activation" and want == "licensed" and not (13 * 86400 < (res.get("remaining_s") or 0) <= 14 * 86400):
    print("GATE FAILED: activation length is not 14 days"); sys.exit(1)
print("GATE PASSED")
