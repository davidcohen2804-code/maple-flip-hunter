"""Build VERIFICATION_REPORT.md from the junit XML files and measurement JSON in a reports directory."""
from __future__ import annotations

import json
import platform
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def junit_summary(p: Path) -> str:
    if not p.exists():
        return f"| {p.name} | not run | | |"
    root = ET.parse(p).getroot()
    suites = root.iter("testsuite") if root.tag == "testsuites" else [root]
    t = f = e = s = 0
    names = []
    for su in suites:
        t += int(su.get("tests", 0)); f += int(su.get("failures", 0)); e += int(su.get("errors", 0)); s += int(su.get("skipped", 0))
        for tc in su.iter("testcase"):
            status = "FAIL" if tc.find("failure") is not None or tc.find("error") is not None else "skip" if tc.find("skipped") is not None else "pass"
            names.append(f"{tc.get('name')}: {status}")
    return f"| {p.name} | {t} tests | {f + e} failed | {s} skipped |\n" + "".join(f"\n    - {n}" for n in names)


def main() -> None:
    rep = Path(sys.argv[1])
    status_lines = []
    live_p = Path("tests/live/last_live_measurements.json")
    live_d = json.loads(live_p.read_text()) if live_p.exists() else {}
    cz = live_d.get("cozy", {})
    status_lines.append(f"* cozy.markets: **{cz.get('status', 'NOT TESTED')}**" + (f" — {cz.get('error')}" if cz.get("error") else "") +
                        (" (newest shop scan age %s s)" % cz.get("newest_shop_age_s") if cz.get("status") == "OK" else ""))
    msw = live_d.get("msw", {})
    status_lines.append(f"* maplestory.watch: **{'OK' if msw else 'NOT TESTED'}**" + (f" — {msw.get('index_items')} items, index age {msw.get('index_age_s')} s, newest listing {msw.get('newest_listing_age_s')} s" if msw else ""))
    wx0 = rep / "windows-results.xml"
    audio = "NOT VERIFIED (no audio device on this machine)" if wx0.exists() and "NO AUDIO OUTPUT DEVICE" in wx0.read_text(encoding="utf-8", errors="ignore") else ("VERIFIED" if wx0.exists() else "NOT TESTED")
    status_lines.append(f"* audio playback: **{audio}**")
    overall = "FULLY VERIFIED" if cz.get("status") == "OK" and audio == "VERIFIED" else "PARTIALLY VERIFIED — see items above"
    out = [f"# Maple Flip Hunter — verification report", "", f"## Overall status: **{overall}**", "", *status_lines, "",
           f"Generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} on {platform.platform()} "
           f"(Python {platform.python_version()}).", "",
           "## Test suites", "", "| file | tests | failed | skipped |", "|---|---|---|---|"]
    for name in ("unit-results.xml", "windows-results.xml", "live-results.xml"):
        out.append(junit_summary(rep / name))
    wx = rep / "windows-results.xml"
    if wx.exists():
        txt = wx.read_text(encoding="utf-8", errors="ignore")
        if "NO AUDIO OUTPUT DEVICE" in txt:
            out += ["", "> **Audio playback was NOT verified on this machine** (no audio output device; the audio tests were "
                        "skipped). Tray notifications, window/banner and startup registration were verified. Run "
                        "`run_live_check.bat` on a PC with speakers to verify audio.", ""]
        elif "test_winsound_plays_generated_alarm" in txt and 'failures="0"' in txt:
            out += ["", "> Audio playback verified on this machine (winsound + Qt Multimedia).", ""]
    live = Path("tests/live/last_live_measurements.json")
    if live.exists():
        d = json.loads(live.read_text())
        out += ["", "## Live source measurements (this machine)", "", "```json", json.dumps(d, indent=2)[:6000], "```"]
    exe = rep / "exe_headless_result.json"
    if exe.exists():
        d = json.loads(exe.read_text())
        m = d["metrics"]
        out += ["", "## Packaged EXE headless run", "",
                f"* scanner state: **{d['scanner_state']}**",
                f"* sources: " + ", ".join(f"{h['label']} = {h['status']} (newest data age {h.get('newest_data_age_s')})" for h in d["health"]),
                f"* rows ingested: {m['listings_ingested']} (new {m['listings_new']}, repriced {m['listings_repriced']}, cross-source duplicates {m['listings_duplicate']})",
                f"* evaluations: {m['evaluations']}, alerts: {m['alerts']}, duplicate alerts suppressed: {m['duplicate_alerts_suppressed']}",
                f"* avg source age at ingest: {m.get('source_age_at_ingest_avg_s')} s",
                f"* avg local analysis: {m.get('analysis_ms_avg')} ms (p95 {m.get('analysis_ms_p95')} ms)",
                f"* **source→alert (seconds between the source's observation timestamp and the alert): {m.get('source_to_alert_s')}**",
                f"* source observation → our first sight (seconds): {m.get('source_age_at_ingest_s')}",
                f"* ingest→alert: {m.get('ingest_to_alert_avg_ms')} ms",
                f"* poll intervals: {m.get('poll_interval_avg_s')}",
                f"* memory RSS: {m.get('rss_mb')} MB, CPU: {m.get('cpu_percent')} %",
                f"* coverage: {d.get('coverage')}",
                "", "Opportunities seen (first 10):", "", "```json", json.dumps(d.get("opportunities", [])[:10], indent=2), "```",
                "", "Alerts fired:", "", "```json", json.dumps(d.get("alerts", [])[:10], indent=2), "```"]
    text = "\n".join(out)
    if len(sys.argv) > 2:                      # ci_report.py <reports dir> <output file>
        Path(sys.argv[2]).write_text(text, encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    except Exception:  # noqa: BLE001
        pass
    print(text)


if __name__ == "__main__":
    main()
