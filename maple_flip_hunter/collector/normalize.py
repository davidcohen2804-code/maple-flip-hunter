"""Item / listing normalization: stable item keys, stats signatures, FM room parsing."""
from __future__ import annotations

import re
from typing import Any, Optional

_WS = re.compile(r"\s+")
_FM_ROOM = re.compile(r"free\s*market\s*<?\s*(\d{1,3})", re.I)
_FM_SHORT = re.compile(r"^\s*FM\s*(\d{1,3})\s*$", re.I)

# stats that change an equipment item's value (scrolled / bonus stats)
VALUE_STATS = ("watk", "matk", "str", "dex", "int", "luk", "wdef", "mdef", "acc", "avoid",
               "speed", "jump", "hp", "mp", "slots")


def normalize_name(name: str) -> str:
    """Case/whitespace-insensitive item name used for cross-source matching."""
    n = _WS.sub(" ", (name or "").strip()).lower()
    n = n.replace("’", "'")
    return n


def stats_signature(stats: Optional[dict[str, Any]]) -> str:
    """Deterministic signature of value-relevant equipment stats ('' when unknown)."""
    if not stats:
        return ""
    parts = []
    for k in VALUE_STATS:
        v = stats.get(k)
        if v is None:
            continue
        try:
            parts.append(f"{k}={int(v)}")
        except (TypeError, ValueError):
            parts.append(f"{k}={v}")
    return ",".join(parts)


def item_key(name: str, category: Optional[str], stats: Optional[dict[str, Any]]) -> str:
    """Stable identifier for a *variant* of an item.

    Non-equipment: normalized name. Equipment: normalized name + stats signature,
    so a scrolled weapon never shares a key with a clean one. Equipment with unknown
    stats gets a '?' suffix and is handled with reduced confidence downstream.
    """
    base = normalize_name(name)
    if (category or "").lower() == "equip":
        sig = stats_signature(stats)
        return f"{base}#{sig}" if sig else f"{base}#?"
    return base


def parse_fm_room(map_name: Optional[str], fm_room: Optional[int] = None) -> Optional[int]:
    if fm_room is not None:
        try:
            return int(fm_room)
        except (TypeError, ValueError):
            pass
    if not map_name:
        return None
    m = _FM_ROOM.search(map_name) or _FM_SHORT.match(map_name)
    return int(m.group(1)) if m else None


def listing_key(source: str, item_key_: str, seller: str, channel: Optional[int], price: int,
                fm_room: Optional[int] = None) -> str:
    """Identity of one shop row. A price change produces a new key (=> may re-alert)."""
    return f"{source}|{item_key_}|{normalize_name(seller)}|{channel if channel is not None else '?'}|{price}"


def cross_source_key(item_key_: str, seller: str, channel: Optional[int], price: int) -> str:
    """Source-independent identity used to merge the same shop row seen by two sources."""
    return f"{item_key_}|{normalize_name(seller)}|{channel if channel is not None else '?'}|{price}"


def stats_match(a: Optional[dict[str, Any]], b: Optional[dict[str, Any]]) -> bool:
    """True when two equipment stat dicts describe the same variant."""
    return stats_signature(a) == stats_signature(b) and stats_signature(a) != ""
