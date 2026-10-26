"""Application bootstrap (GUI)."""
from __future__ import annotations

import logging
import sys

from . import APP_NAME
from .config import Settings
from .db import Database
from .logging_setup import setup_logging
from .paths import db_path

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argv = list(sys.argv if argv is None else argv)
    if "--headless" in argv:  # no GUI: run the engine for N seconds and write measurements (CI / diagnostics)
        import json

        from .headless import run as headless_run

        secs = float(argv[argv.index("--seconds") + 1]) if "--seconds" in argv else 60.0
        out = argv[argv.index("--out") + 1] if "--out" in argv else "headless_result.json"
        settings_file = argv[argv.index("--settings") + 1] if "--settings" in argv else None
        res = headless_run(secs, settings_file=settings_file)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, default=str)
        return 0
    from PySide6.QtWidgets import QApplication

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    settings = Settings.load()
    from .edition import is_friends

    license_client = None
    if is_friends() or "--friends" in argv:
        from .licensing import LicenseClient
        from .ui.lock_dialog import LockDialog

        server = argv[argv.index("--license-server") + 1] if "--license-server" in argv else ""
        pub = argv[argv.index("--license-pubkey") + 1] if "--license-pubkey" in argv else ""
        license_client = LicenseClient(server=server, public_key=pub)
        st = license_client.ensure()
        if "--license-check" in argv:            # CI / support: write the decision to a file and exit (0 ok, 3 locked)
            import json

            out = argv[argv.index("--license-check") + 1]
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"status": st.status, "kind": st.kind, "expires_at": st.expires_at, "remaining_s": st.remaining_s,
                           "remaining_text": st.remaining_text, "message": st.message, "online": st.online,
                           "machine": license_client.machine[:12]}, f)
            return 0 if st.status in ("trial", "licensed") else 3
        if st.status not in ("trial", "licensed"):
            dlg = LockDialog(license_client, reason=st.message)
            if not dlg.exec() or not dlg.unlocked:
                return 3
    db = Database(db_path())
    from .collector.engine import MarketEngine
    from .notifications import Notifier
    from .ui.main_window import MainWindow

    notifier = Notifier(volume=settings.alert_volume)
    engine = MarketEngine(settings, db, notifier)
    win = MainWindow(engine, settings, notifier)
    if license_client is not None:
        win.attach_license(license_client)
    if settings.start_minimized or "--minimized" in argv:
        win.hide()
    else:
        win.show()
    if "--no-autostart" not in argv:
        engine.start()
        win.btn_start.setText("⏹  Stop scanner")
    rc = app.exec()
    engine.stop()
    db.close()
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
