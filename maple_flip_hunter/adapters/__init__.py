from .base import BaseAdapter, FetchError, RateLimited, TokenBucket  # noqa: F401
from .cozy import CozyAdapter  # noqa: F401
from .maplestory_watch import MapleStoryWatchAdapter  # noqa: F401

# Sources investigated but NOT integrated (see docs/SOURCE_AUDIT.md):
UNSUPPORTED_SOURCES = {
    "freemarketclassic.com": "Terms of use forbid bots/scripts on the website; no public API.",
    "maplemarket.fyi": "Paid subscription; terms forbid republishing/scraping; no public API.",
    "maplefm.gg": "Terms require written permission for automated collection (they say they usually grant it).",
    "beta.meso.watch": "Site disallows automated access (robots).",
}
