# Access requests — ready-to-send messages to data-source operators

Context for every request: Maple Flip Hunter is a personal, non-commercial Windows desktop tool for one player.
It never automates the game, never trades, and only *reads* market data. It identifies itself with the
User-Agent `MapleFlipHunter/<version>` and keeps a self-imposed rate limit. Nothing in it circumvents bot
protection; a source that says no is simply left disabled.

Send order (fastest potential gain first): **1 cozy.markets → 2 Club FM → 3 maplestory.watch → 4 Kerning Exchange**.
After a "yes": cozy → paste the token (if any) in Settings → cozy.markets token; Club FM → tick
"Club FM — needs their written permission" (+ token if issued); keep the reply as proof.

---

## 1. cozy.markets — Discord https://discord.gg/zn8UhPYRN (guild server; ask in the FM Overlay / site channel)

> Hi! I'm a MapleStory Classic player and I built a small personal desktop tool (Windows, Python) that watches the
> Free Market for badly underpriced shop listings and plays a sound so I can run to the shop. It does not automate
> the game or trading in any way — it only reads market data and alerts me.
>
> Your board is by far the freshest source I found (shop scans show up within seconds), and your JSON
> (`/api/shops`, `/api/shops/{shop}/listings`, `/api/deals`, `/api/items/{id}`) is exactly what I need.
> Right now my tool gets **HTTP 403** from Cloudflare on those endpoints, both from my home PC and from a build
> server, while the same URLs work in a browser. I don't want to work around your protection, so I'm asking
> permission instead.
>
> Would you be willing to either (a) allow requests with the User-Agent `MapleFlipHunter/0.4 (+personal tool)`,
> or (b) issue me an API token I can send as `Authorization: Bearer …`? My usage: one poll of `/api/shops` and
> `/api/deals` every 10 s and `/api/shops/{shop}/listings` only for shops that changed (typically 5–30 requests per
> minute, hard-capped at 3 requests/second, honest User-Agent, no caching bypass). If you'd prefer a slower
> cadence or different endpoints, I'll follow whatever you say. I'm also happy to run FM Overlay and contribute scans.
>
> Thanks a lot for the board — and for considering this.

---

## 2. Club FM (maplefm.gg) — Discord https://discord.gg/cXnVBTUUPk or the site's Feedback button

> Hi! Your Terms of Use say automated collection needs permission first and that you usually say yes, so here
> goes. I'm a Classic player with a personal, non-commercial Windows tool that watches the Free Market for
> badly underpriced listings and plays an alarm. It never touches the game; it only reads market data.
>
> May I read your public snapshot (`https://api.maplefm.gg/data/snap`) once per minute with the User-Agent
> `MapleFlipHunter/0.4 (+personal tool)`? If you're open to it, access to the incremental `/data/live` feed
> (with whatever token/sign-in you use) would make the alerts a lot faster — I would poll it no more than once a
> minute. The data stays on my PC; nothing is republished, mirrored or used for training. If you'd like credit
> shown in the tool ("prices from Club FM") I'll add it. I'm also happy to run FM Watcher and contribute scans.
>
> Thank you — the board is great.

---

## 3. maplestory.watch — Discord https://discord.gg/tdgvx66gk6 (the address their robots.txt / Terms give for data requests)

**Status 2026-10-27: STOP — the site updated its Terms today.** `/api/` is declared *not* a public API; scripts, bots and
apps may not read it; requests not coming from the site's pages are blocked (HTTP 403); getting around a block is
forbidden; copying/republishing the index is forbidden. The adapter therefore no longer polls (status UNSUPPORTED)
until written permission is recorded. Message to send on their Discord:

> Hi! I'm David, a MapleStory Classic player. I built a small personal Windows tool that watches the Free Market
> for badly under-priced shop listings and plays a sound so I can go buy them. It never touches the game (no
> memory reading, no screen capture, no automation) and never republishes your data — each user's copy only shows
> the current listing it is alerting about.
>
> I read your updated Terms (Oct 27) and robots.txt: /api/ is not public and apps are blocked. I respect that —
> my tool has stopped calling it. I'm asking for permission instead of working around anything.
>
> Would you allow my tool to read `/api/items` (the index, once every 3 minutes) and `/api/item?name=…` (only for
> items whose lowest price is far under typical, at most 40 lookups per 3 minutes, 1 request/second max, honest
> User-Agent `MapleFlipHunter/<version>`), for me and a handful of friends (under 30 people)? If you'd rather issue
> a token, a slower cadence, a specific endpoint, or say no — I'll follow whatever you decide, and I'm glad to
> credit maplestory.watch in the app and contribute shop scans back. Thank you for building the index!

After a written "yes": record it in Settings → Sources → "maplestory.watch — written permission" (+ who/when in
the note), and for the Friends build add `"msw_permission": true` to `friends_config.json` at build time.

## 4. Kerning Exchange (kerning.gg) — Discord https://discord.gg/wrWgmK2Czk (optional)

> Hi! I'm building a personal Free Market alert tool for Classic and I like your live smega feed. Do you expose
> (or would you allow) a JSON feed of recent smega listings — item, price, seller, timestamp — that a personal
> desktop tool may poll about once a minute with the User-Agent `MapleFlipHunter/0.4 (+personal tool)`?
> Nothing is republished; it only triggers an alarm on my PC. Happy to follow any rate limit you set.

---

## 5. HubCW (hubcw.com) — e-mail po@hubcw.com (Terms §6: written permission for automated access)

Subject: Permission request — read-only access to HubCW listings for a personal desktop tool

Hi HubCW team,

I run Maple Flip Hunter, a small personal Windows tool that watches the Classic World Free Market for under-priced
listings and plays a sound. It reads only; it never posts, bumps, rates, messages or touches the game client.

I would like written permission (Terms §6) for read-only automated access to the public listing board and Price Check
data (item, price, seller, channel/room, stats, post time), polled no more than once per minute, with a clear
User-Agent (`MapleFlipHunter/<version>`), and I will link back to HubCW on every item the tool shows. If you have a
preferred endpoint, token or rate limit, I will use exactly that. If the answer is no, I will not access HubCW
automatically at all.

Thank you for the site,
David

## Not asked
* **freemarketclassic.com** — their Terms forbid automating the website; they offer their own alert app (FM Sync),
  which is the sanctioned way to get alerts from that data.
* **maplemarket.fyi** — paid, and the Terms forbid republishing/scraping; no API offered.
* **beta.meso.watch / meso.watch** — disallow automated access; nothing to verify.
* **evileyefm.com** — the page talks to a third-party database with a key embedded in its JavaScript; using that key
  from another program would not be authorized use, and the site shows almost no coverage (12 rows in 7 days).
