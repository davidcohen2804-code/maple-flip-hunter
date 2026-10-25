# Known limitations

1. **Coverage is community-scanned.** Only shops that another player's scanner (cozy MaplePrice app,
   maplestory.watch browser companion) has seen are visible. Rooms/channels nobody walks are blind spots.
   The dashboard shows how many items/rows were seen; it never claims full coverage.
2. **No seller-publication timestamp.** Sources report when *they* saw a row. "First observed" is the scanner's
   time, displayed as such; the seller may have listed earlier.
3. **Freshness ceiling.** cozy: 5 s CDN cache + 10 s poll; maplestory.watch: 3–5 min cache. The app cannot be faster
   than its sources (docs/DATA_FRESHNESS.md). Not "real-time relative to the game".
4. **Equipment variants.** Stats are only present when the scanning player hovered the item. Rows without stats are
   capped at LOW confidence (UNCERTAIN, no alarm). Scroll *percentage* is part of the item name in Classic
   ("…Scroll: Intermediate" = 60 %, "Greater" = 10 %), so scrolls are matched by exact name.
5. **Completed sales are inferred by the sources** (greyed-out row / stock drop). We label them "sales" because the
   sources do, but a disappearing listing in *our* database is never counted as a sale.
6. **Cross-source dedupe** uses item + seller + channel + price. Two different shops of the same seller on the same
   channel with the same price would merge; this is rare and conservative (fewer alerts, not more).
7. **maplefm.gg / freemarketclassic / maplemarket** are not integrated (terms of service / paid). See SOURCE_AUDIT.md.
8. **Windows notifications** use the system-tray balloon API (`QSystemTrayIcon.showMessage`), which Windows 10/11
   renders as a toast. Focus Assist / "Do not disturb" can hide toasts; the alarm sound still plays.
9. **Audio** requires a working default output device. Qt Multimedia is used; if it fails, `winsound` plays the WAV
   at system volume (the in-app volume slider then has no effect).
10. **Rate limits are self-imposed** (neither source publishes limits). If a source starts returning 429 the adapter
    backs off automatically and the UI shows RATE LIMITED.
11. **The Windows EXE was built and smoke-tested on a GitHub Actions Windows runner**, not on a physical gaming PC
    (see docs/TEST_RESULTS.md for the exact evidence). Antivirus heuristics sometimes flag unsigned PyInstaller
    executables; `run_dev.bat` runs the same code from source if that happens.
12. A UI session shows opportunities from the last 6 hours at startup; older history stays in the database
    (`%APPDATA%\MapleFlipHunter\market.sqlite3`).
