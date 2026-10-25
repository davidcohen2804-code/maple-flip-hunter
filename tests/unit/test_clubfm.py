"""Club FM adapter: decoding of the columnar snapshot and the permission gate (adapter never polls without it)."""
from __future__ import annotations

import asyncio

import httpx

from maple_flip_hunter.adapters.clubfm import ClubFMAdapter, decode_snapshot
from maple_flip_hunter.models import SourceStatus

T_MS = 1_792_951_340_000
SNAP = {
    "v": 2, "at": T_MS + 326_000, "T": T_MS, "n": 3,
    "cols": {"i": [0, 0, 1], "s": [0, 1, 2], "p": [159999, 149999, 999], "q": [1, 1, 1], "u": [159999, 149999, 999],
             "r": [0, 0, 1], "c": [19, 19, 0], "t": [363, 382, 218], "st": [0, 1, 1], "sold": [0, 1, 0], "chat": [0, 0, 0]},
    "words": {"i": ["Frameless Glasses", "Spear Attack Scroll: Greater"], "s": ["Purpo", "Full", "Tsubomi"],
              "r": ["FM2", "FM"], "st": ["old", "up"]},
    "kinds": {"i": "w", "s": "w", "p": "v", "q": "v", "u": "v", "r": "w", "c": "v", "t": "t", "st": "w", "sold": "v", "chat": "v"},
    "D": {"active": [{"@": 0}, {"@": 2}], "sales": [{"@": 1}], "latest": T_MS},
}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _adapter(**kw):
    def handler(req):
        if req.url.path == "/data/snap":
            return httpx.Response(200, json=SNAP)
        return httpx.Response(404)
    a = ClubFMAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), **kw)
    a.bucket.rate = 1000
    return a


def test_decode_snapshot_words_and_times():
    rows = decode_snapshot(SNAP)
    assert rows[0]["i"] == "Frameless Glasses" and rows[0]["s"] == "Purpo" and rows[0]["r"] == "FM2"
    assert abs(rows[0]["t"] - (T_MS / 1000 - 363)) < 1e-6
    assert rows[2]["st"] == "up"


def test_without_permission_the_adapter_never_requests_anything():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json=SNAP)
    a = ClubFMAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"))
    assert a.health.status == SourceStatus.UNSUPPORTED
    assert run(a.poll()) == []
    assert calls == []


def test_with_permission_active_rows_and_sales_are_emitted():
    a = _adapter(permission_granted=True, token="abc")
    assert a.client.headers["Authorization"] == "Bearer abc"
    out = run(a.poll())
    assert {(l.item_name, l.price, l.sold) for l in out} == {("Frameless Glasses", 159999, False),
                                                              ("Frameless Glasses", 149999, True),
                                                              ("Spear Attack Scroll: Greater", 999, False)}
    g = next(l for l in out if l.price == 159999)
    assert g.channel == 19 and g.fm_room == 2 and g.seller == "Purpo"
    assert a.health.status in (SourceStatus.ONLINE, SourceStatus.STALE)
    assert run(a.poll()) == []                       # same snapshot -> nothing re-emitted
    ev = run(a.fetch_evidence(g))
    assert ev.sold_median == 149999 and ev.sold_count == 1
    assert all(not (c.seller == "Purpo" and c.unit_price == 159999) for c in ev.active_asks)
