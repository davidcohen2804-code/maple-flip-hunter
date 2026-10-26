from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))
import license_server as srv  # noqa: E402

from maple_flip_hunter.licensing import LicenseClient  # noqa: E402
from maple_flip_hunter.ui.lock_dialog import CONTACT_EMAIL, LockDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_lock_dialog_message_copy_and_activation(app, tmp_path, monkeypatch):
    for name, attr in (("k.pem", "KEY_FILE"), ("pub.txt", "PUB_FILE"), ("lic.sqlite3", "DB_FILE"), ("m.json", "MANIFEST")):
        monkeypatch.setattr(srv, attr, tmp_path / name)
    srv.init()
    s = srv.serve(0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{s.server_address[1]}"
        c = LicenseClient(server=url, public_key=(tmp_path / "pub.txt").read_text().strip(),
                          state_file=tmp_path / "st.json", registry_key=r"Software\MFHTest\lock_" + __import__("uuid").uuid4().hex)
        c.ensure()
        real = srv.time.time
        monkeypatch.setattr(srv.time, "time", lambda: real() + 73 * 3600)
        c.refresh()
        assert c.state().status == "expired"
        dlg = LockDialog(c, reason=c.state().message)
        texts = [w.text() for w in dlg.findChildren(type(dlg.reason))]
        assert any("Your 3-day free trial has expired." in t for t in texts)
        assert any("please contact" in t for t in texts) and any(CONTACT_EMAIL in t for t in texts)
        dlg.btn_copy.click()
        assert QApplication.clipboard().text() == CONTACT_EMAIL and dlg.btn_copy.text() == "✓ Copied"
        dlg.code.setText("BAD-CODE")
        dlg.activate()
        assert "Activation failed" in dlg.result_lbl.text() and not dlg.unlocked
        dlg.code.setText(srv.make_code(7))
        dlg.activate()
        assert dlg.unlocked and c.state().status == "licensed"
    finally:
        s.shutdown()
