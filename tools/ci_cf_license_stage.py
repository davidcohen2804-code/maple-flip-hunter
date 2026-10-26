"""CI stage: the built Friends EXE against the Cloudflare Worker running LOCALLY (wrangler dev --local = miniflare,
no Cloudflare account, no network). Same path a friend walks: first start → 72 h trial → trial start moved back 73 h
in D1 → locked (exit 3) → 14-day code → licensed → code expired → locked → lifetime code → licensed → revoked → locked.

    python tools/ci_cf_license_stage.py <url> <admin_token> <out_dir> -- <exe or command...>
e.g. python tools/ci_cf_license_stage.py http://127.0.0.1:8798 testadmin reports -- dist\\MapleFlipHunterFriends.exe
The command gets `--license-server <url> --license-pubkey <key> --license-check <file>` appended.
Needs `wrangler` on PATH (local D1 edits) and the worker's public key from /v1/health."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
WORKER_DIR = HERE.parent / "server" / "cloudflare"


def d1(sql: str) -> None:
    wr = shutil.which("wrangler") or shutil.which("wrangler.cmd")
    subprocess.run([wr, "d1", "execute", "mfh-licenses", "--local", "--command", sql], cwd=WORKER_DIR, check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")


def run_check(cmd: list[str], url: str, pub: str, out: Path, label: str, expect_exit: int, expect_status: str,
              expect_kind: str | None = None) -> dict:
    env = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen" if sys.platform != "win32" else "")}
    env = {k: v for k, v in env.items() if v != ""}
    p = subprocess.run(cmd + ["--license-server", url, "--license-pubkey", pub, "--license-check", str(out)],
                       env=env, timeout=120)
    data = json.loads(out.read_text(encoding="utf-8"))
    print(f"{label}: exit {p.returncode} -> {data['status']} ({data['kind']}) {data.get('remaining_text') or data.get('message')}")
    assert p.returncode == expect_exit, f"{label}: expected exit {expect_exit}, got {p.returncode}"
    assert data["status"] == expect_status, f"{label}: expected status {expect_status}, got {data['status']}"
    if expect_kind:
        assert data["kind"] == expect_kind, f"{label}: expected kind {expect_kind}, got {data['kind']}"
    return data


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows console is cp1252 by default
    except Exception:  # noqa: BLE001
        pass
    i = sys.argv.index("--")
    url, admin, out_dir = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3])
    cmd = sys.argv[i + 1:]
    out_dir.mkdir(parents=True, exist_ok=True)
    H = {"Authorization": f"Bearer {admin}"}
    health = httpx.get(url + "/v1/health", timeout=20).json()
    pub = health["public_key"]
    assert pub and len(pub) == 64, health

    # machine id must be fresh: wipe this runner's state so the EXE registers anew
    d1("DELETE FROM machines; DELETE FROM codes;")
    for f in ("friends_license.json",):
        p = Path(os.environ.get("APPDATA", str(Path.home()))) / "MapleFlipHunter" / f
        if p.exists():
            p.unlink()
    if sys.platform == "win32":
        subprocess.run(["reg", "delete", r"HKCU\Software\MapleFlipHunter\Friends", "/f"], capture_output=True)

    d1_ = run_check(cmd, url, pub, out_dir / "cf1.json", "1 first start", 0, "trial", "trial")
    machine_prefix = d1_["machine"]
    d1("UPDATE machines SET first_seen = first_seen - 73*3600")
    run_check(cmd, url, pub, out_dir / "cf2.json", "2 trial 73 h old", 3, "expired", "trial")
    code = httpx.post(url + "/admin/code", json={"days": 14}, headers=H, timeout=20).json()["code"]
    # redeem exactly like the lock screen does (same client code path)
    sys.path.insert(0, str(HERE.parent))
    from maple_flip_hunter.licensing import LicenseClient
    c = LicenseClient(server=url, public_key=pub)
    ok, msg = c.redeem(code)
    assert ok, msg
    run_check(cmd, url, pub, out_dir / "cf3.json", "3 after 14-day code", 0, "licensed", "activation")
    d1("UPDATE machines SET licensed_until = licensed_until - 15*86400 WHERE licensed_until > 0")
    run_check(cmd, url, pub, out_dir / "cf4.json", "4 activation 15 d old", 3, "expired", "activation")
    ok, msg = c.redeem(httpx.post(url + "/admin/code", json={"days": 0}, headers=H, timeout=20).json()["code"])
    assert ok, msg
    run_check(cmd, url, pub, out_dir / "cf5.json", "5 lifetime", 0, "licensed", "lifetime")
    r = httpx.post(url + "/admin/revoke", json={"machine": machine_prefix, "revoked": True}, headers=H, timeout=20).json()
    assert r.get("revoked") is True, r
    run_check(cmd, url, pub, out_dir / "cf6.json", "6 revoked", 3, "expired", "revoked")
    httpx.post(url + "/admin/revoke", json={"machine": machine_prefix, "revoked": False}, headers=H, timeout=20)
    print("OK: Cloudflare worker licence path (trial -> lock -> 14-day -> lock -> lifetime -> revoke) passed")
    (out_dir / "cf_license_stage.txt").write_text("ok " + time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
