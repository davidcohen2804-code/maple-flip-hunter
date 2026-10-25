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
    db = Database(db_path())
    from .collector.engine import MarketEngine
    from .notifications import Notifier
    from .ui.main_window import MainWindow

    notifier = Notifier(volume=settings.alert_volume)
    engine = MarketEngine(settings, db, notifier)
    win = MainWindow(engine, settings, notifier)
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
