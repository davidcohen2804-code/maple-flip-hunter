# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Maple Flip Hunter (one-folder build, windowed).
# Build with:  pyinstaller build/windows/MapleFlipHunter.spec   (run from the project root)
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

a = Analysis(
    [os.path.join(ROOT, "run_app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("maple_flip_hunter") + ["PySide6.QtMultimedia", "PySide6.QtNetwork"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MapleFlipHunter",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # GUI app; --headless writes a JSON file instead of printing
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MapleFlipHunter")
