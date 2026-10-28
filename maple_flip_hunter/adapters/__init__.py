from .base import BaseAdapter, FetchError, RateLimited, TokenBucket  # noqa: F401
from .clubfm import ClubFMAdapter  # noqa: F401
from .cozy import CozyAdapter  # noqa: F401
from .fmwatcher import FMWatcherAdapter  # noqa: F401
from .maplestory_watch import MapleStoryWatchAdapter  # noqa: F401
from .public_market import PublicMarketAdapter  # noqa: F401
from .hene_public import HenePublicAdapter  # noqa: F401

# Sources investigated but NOT integrated (see docs/SOURCE_AUDIT.md):
UNSUPPORTED_SOURCES = {
    "maplemarket.fyi": "Paid subscription; terms forbid republishing/scraping; no public API.",
    "maplefm.gg": "Adapter built (adapters/clubfm.py) but disabled until written permission is recorded in Settings (Terms §2).",
    "kerning.gg": "Smega (chat) ads only - no channel / FM room; no terms published; Discord contact. Not integrated.",
    "evileyefm.com": "Third-party database key in page JS; ~no coverage (12 rows in 7 days). Not integrated.",
    "beta.meso.watch": "Site disallows automated access (robots).",
}
