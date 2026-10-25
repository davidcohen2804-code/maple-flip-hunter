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
    out = [f"# Maple Flip Hunter — verification report", "",
           f"Generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} on {platform.platform()} "
           f"(Python {platform.python_version()}).", "",
           "## Test suites", "", "| file | tests | failed | skipped |", "|---|---|---|---|"]
    for name in ("unit-results.xml", "windows-results.xml", "live-results.xml"):
        out.append(junit_summary(rep / name))
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
                f"* avg source→alert: {m.get('source_to_alert_avg_s')} s; ingest→alert: {m.get('ingest_to_alert_avg_ms')} ms",
                f"* poll intervals: {m.get('poll_interval_avg_s')}",
                f"* memory RSS: {m.get('rss_mb')} MB, CPU: {m.get('cpu_percent')} %",
                f"* coverage: {d.get('coverage')}",
                "", "Opportunities seen (first 10):", "", "```json", json.dumps(d.get("opportunities", [])[:10], indent=2), "```",
                "", "Alerts fired:", "", "```json", json.dumps(d.get("alerts", [])[:10], indent=2), "```"]
    print("\n".join(out))


if __name__ == "__main__":
    main()
