"""Friends Edition licensing: trial from first server activation, expiry, lock, activation code, clock rollback,
reinstall persistence, offline grace, signed update manifest. Runs a real licence server in-process."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
import license_server as srv  # noqa: E402

from maple_flip_hunter.licensing import LicenseClient  # noqa: E402
from maple_flip_hunter.licensing import update as upd  # noqa: E402
from maple_flip_hunter.licensing.tokens import load_public_key, sign, verify  # noqa: E402


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "KEY_FILE", tmp_path / "k.pem")
    monkeypatch.setattr(srv, "PUB_FILE", tmp_path / "pub.txt")
    monkeypatch.setattr(srv, "DB_FILE", tmp_path / "lic.sqlite3")
    monkeypatch.setattr(srv, "MANIFEST", tmp_path / "manifest.json")
    srv.init()
    s = srv.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}", (tmp_path / "pub.txt").read_text().strip()
    s.shutdown()


import uuid

_REG = {}


def _client(server, tmp_path, name="a"):
    """One isolated registry key per test (on Windows the HKCU copy is what survives reinstalls)."""
    url, pub = server
    key = _REG.setdefault(str(tmp_path), "Software\\MFHTest\\" + uuid.uuid4().hex)
    return LicenseClient(server=url, public_key=pub, state_file=tmp_path / f"{name}.json", registry_key=key)


def test_trial_starts_on_server_and_survives_reinstall(server, tmp_path):
    c = _client(server, tmp_path)
    st = c.ensure()
    assert st.status == "trial" and 71.9 * 3600 < st.remaining_s <= 72 * 3600 and st.online
    first_exp = st.expires_at
    # "reinstall": delete every local file AND the registry copy → the server still knows this machine; no new 72 h
    os.unlink(c.state_file)
    c2 = _client(server, tmp_path, "b")
    c2.token, c2.last_server_time, c2.last_contact = None, 0.0, 0.0
    st2 = c2.ensure()
    assert st2.status == "trial" and abs(st2.expires_at - first_exp) < 1


def test_expiry_lock_and_activation_code(server, tmp_path, monkeypatch):
    c = _client(server, tmp_path)
    assert c.ensure().status == "trial"
    # the server's clock moves 73 h forward → expired on the next status call (client clock irrelevant)
    real_time = time.time
    monkeypatch.setattr(srv.time, "time", lambda: real_time() + 73 * 3600)
    assert c.refresh() and c.state().status == "expired"
    # bogus / foreign code
    ok, msg = c.redeem("NOPE-0000")
    assert not ok and "unknown" in msg
    code = srv.make_code(30)
    ok, msg = c.redeem(code)
    assert ok and c.state().status == "licensed"
    assert c.state().remaining_s > 29 * 86400 - 73 * 3600 - 60
    # the same code on another machine is refused
    other = _client(server, tmp_path, "other")
    other.machine = "f" * 64
    ok2, msg2 = other.redeem(code)
    assert not ok2 and "another computer" in msg2
    lifetime = srv.make_code(0)
    assert other.redeem(lifetime)[0] and other.state().status == "licensed" and other.state().expires_at is None


def test_clock_rollback_and_offline_grace(server, tmp_path, monkeypatch):
    c = _client(server, tmp_path)
    assert c.ensure().status == "trial"
    # offline (server unreachable): cached signed token keeps working …
    c.server = "http://127.0.0.1:9"
    assert not c.refresh() and c.state().status == "trial"
    # … unless the local clock is moved back below the last server time
    real = time.time
    import maple_flip_hunter.licensing.client as cl
    monkeypatch.setattr(cl.time, "time", lambda: real() - 3600)
    assert c.state().status == "locked" and "clock" in c.state().message
    # … or the trial really ran out (trusted time moves forward beyond exp)
    monkeypatch.setattr(cl.time, "time", lambda: real() + 73 * 3600)
    st = c.state()
    assert st.status in ("expired", "locked")        # expired by exp, or locked by the 24 h offline grace
    # offline for more than 24 h → locked even inside the trial
    monkeypatch.setattr(cl.time, "time", lambda: real() + 25 * 3600)
    assert c.state().status == "locked" and "24 hours" in c.state().message


def test_token_cannot_be_forged_or_reused_on_another_machine(server, tmp_path):
    c = _client(server, tmp_path)
    assert c.ensure().status == "trial"
    pub = load_public_key(server[1])
    payload = verify(c.token, pub)
    assert payload["m"] == c.machine and payload["st"] == "trial"
    # tamper with the payload → signature fails
    body, sig = c.token.split(".")
    import base64
    raw = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    raw["exp"] = time.time() + 10 * 365 * 86400
    forged = base64.urlsafe_b64encode(json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()).rstrip(b"=").decode() + "." + sig
    assert verify(forged, pub) is None
    c.token = forged
    assert c.state().status == "unknown"
    # a valid token copied from another machine is rejected by the machine binding
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    other_key = Ed25519PrivateKey.generate()
    assert verify(sign({"m": c.machine, "st": "licensed", "exp": None, "iat": time.time(), "ver": 1}, other_key), pub) is None


def test_update_manifest_is_verified(server, tmp_path):
    url, pub_hex = server
    exe = tmp_path / "new.exe"
    exe.write_bytes(b"fake exe bytes")
    srv.publish_update("99.0.0", f"{url}/does-not-matter", str(exe))
    pub = load_public_key(pub_hex)
    m = upd.check(url, pub)
    assert m and m["version"] == "99.0.0"
    tampered = dict(m, url="https://evil.example/x.exe")
    assert not upd.verify_manifest(tampered, pub)
    # same version or older → no update offered
    srv.publish_update("0.0.1", f"{url}/x", str(exe))
    assert upd.check(url, pub) is None
    # sha mismatch on download → discarded
    srv.publish_update("99.0.1", f"{url}/v1/health", str(exe))   # url serves JSON, not the exe bytes
    assert upd.download(upd.check(url, pub)) is None


def test_developer_edition_is_unrestricted(monkeypatch):
    from maple_flip_hunter import edition
    monkeypatch.delenv("MFH_EDITION", raising=False)
    assert edition.edition() == "developer" and not edition.is_friends()
    monkeypatch.setenv("MFH_EDITION", "friends")
    assert edition.is_friends()
