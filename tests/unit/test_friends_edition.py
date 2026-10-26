"""Distribution hardening for the Friends Edition: physical features can never be switched on, revoked machines are
locked, admin commands (check / revoke / unrevoke / backup) work, abusive clients are rate limited, and the release
binary check refuses placeholder / local servers."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
import license_server as srv  # noqa: E402

from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.licensing import LicenseClient  # noqa: E402
from tests.unit.test_licensing import _client, server  # noqa: E402,F401


def test_friends_edition_forces_physical_features_off(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    s = Settings()
    s.physical_features_enabled = True
    s.source_fmwatcher_autostart = True
    s.source_fmwatcher_enabled = True
    s.source_clubfm_base_url = "http://127.0.0.1:8797"      # a simulator address left in settings.json
    p.write_text(s.model_dump_json())
    monkeypatch.setenv("MFH_EDITION", "developer")
    dev = Settings.load(p)
    assert dev.physical_features_enabled is True            # developer edition honours the file
    monkeypatch.setenv("MFH_EDITION", "friends")
    fr = Settings.load(p)
    assert fr.physical_features_enabled is False and fr.source_fmwatcher_autostart is False
    assert fr.source_fmwatcher_enabled is False and fr.source_clubfm_base_url.startswith("https://api.maplefm.gg")


def test_revoke_check_unrevoke_and_backup(server, tmp_path, capsys):
    c = _client(server, tmp_path)
    assert c.ensure().status == "trial"
    prefix = c.machine[:12]
    srv.describe(prefix)
    assert "trial" in capsys.readouterr().out
    srv.set_revoked(prefix, True)
    assert c.refresh()
    st = c.state()
    assert st.status == "expired" and st.kind == "revoked" and "revoked" in st.message
    # a code does not un-revoke: only the admin does
    ok, msg = c.redeem(srv.make_code(14))
    assert not ok and "revoked" in msg
    srv.set_revoked(prefix, False)
    assert c.refresh() and c.state().status == "trial"
    # unknown / ambiguous prefix is refused rather than guessing
    with pytest.raises(SystemExit):
        srv.find_machine("zzzzzz")
    b = srv.backup()
    assert b.exists() and b.stat().st_size > 0 and b.parent.name == "backups"


def test_lifetime_survives_version_change_and_list(server, tmp_path, capsys):
    c = _client(server, tmp_path)
    c.ensure()
    assert c.redeem(srv.make_code(0))[0]
    c.version = "9.9.9"                                        # app updated: same machine, same licence
    assert c.refresh() and c.state().status == "licensed" and c.state().kind == "lifetime"
    rows = srv.db().execute("SELECT version FROM machines").fetchall()
    assert rows[0]["version"] == "9.9.9"


def test_rate_limit_per_ip(server, monkeypatch):
    url, _pub = server
    monkeypatch.setattr(srv, "_hits", {})
    monkeypatch.setattr(srv, "RATE_PER_MIN", 5)
    codes = [httpx.post(url + "/v1/status", json={"machine": "a" * 64, "version": "1"}, timeout=5).status_code
             for _ in range(8)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429] * 3
    # a different IP header (behind Caddy the real IP arrives in X-Forwarded-For) has its own budget
    r = httpx.post(url + "/v1/status", json={"machine": "a" * 64, "version": "1"},
                   headers={"X-Forwarded-For": "203.0.113.9"}, timeout=5)
    assert r.status_code == 200
    big = httpx.post(url + "/v1/status", content=b"x" * 5000, headers={"X-Forwarded-For": "203.0.113.10"}, timeout=5)
    assert big.status_code == 413


def test_binary_check_refuses_local_or_placeholder_server(tmp_path, monkeypatch):
    import check_friends_binary as chk

    def fake_files(_exe):
        return {"friends_config.json": json.dumps(cfg).encode(), "_edition.txt": b"friends"}

    monkeypatch.setattr(chk, "bundled_files", fake_files)
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"MZ clean binary")
    cfg = {"server": "https://license.mydomain.net", "public_key": "ab" * 32}
    assert chk.main(str(exe)) == 0
    for bad in ("http://license.mydomain.net", "https://127.0.0.1:8797", "https://license.example.com", "https://localhost"):
        cfg = {"server": bad, "public_key": "ab" * 32}
        assert chk.main(str(exe)) == 1, bad
    cfg = {"server": "https://license.mydomain.net", "public_key": "short"}
    assert chk.main(str(exe)) == 1
    cfg = {"server": "https://license.mydomain.net", "public_key": "ab" * 32}
    exe.write_bytes(b"MZ ... http://127.0.0.1:8797 ...")        # CI leftover inside the binary
    assert chk.main(str(exe)) == 1
