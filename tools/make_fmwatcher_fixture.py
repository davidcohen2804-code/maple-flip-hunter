"""CI helper: write a fresh FM Watcher-format store into <folder> with one obviously under-priced shop
(an item that exists on maplestory.watch so the live index can price it) plus ordinary rows."""
import json, os, sys, time
folder = sys.argv[1]
os.makedirs(folder, exist_ok=True)
now = time.time()
day = time.strftime("%Y-%m-%d", time.localtime(now))
def entry(seller, item, price, qty, nu, so, room, ch, age):
    return json.dumps([day, seller, item, price, qty]), {"n": nu or 1, "reads": 3, "first": now - age - 30, "last": now - age,
            "known": True, "so": so, "so_reads": 0, "nu": nu, "ns": 0, "s_last": 0, "hi_t": now - age, "r": room, "c": ch}
shop = dict([
    entry("NoobTrader", "Hat Accuracy Scroll: Intermediate", 2_000, 1, 1, False, "FM3", 7, 5),   # typical ~30k on msw
    entry("Pricey", "King Slime Balloon", 49_999, 1, 1, False, "FM1", 35, 20),
    entry("Bundler", "Mithril Ore", 3_000, 10, 2, False, "FM1", 2, 40),
])
with open(os.path.join(folder, "watcher_data_v2.json"), "w", encoding="utf-8") as f:
    json.dump({"shop": shop, "chat": {}, "sellers": {}, "mins": {}}, f)
print("fixture written:", folder)
