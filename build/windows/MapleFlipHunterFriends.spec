# -*- mode: python ; coding: utf-8 -*-
# Friends Edition: ONE-FILE windowed EXE. Bundles maple_flip_hunter/_edition.txt ("friends") and friends_config.json
# (licence server URL + PUBLIC key). No private keys, no provider credentials.
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
PKG = os.path.join(ROOT, "maple_flip_hunter")
datas = [(os.path.join(PKG, "_edition.txt"), "maple_flip_hunter")]
if os.path.exists(os.path.join(PKG, "friends_config.json")):
    datas.append((os.path.join(PKG, "friends_config.json"), "maple_flip_hunter"))

a = Analysis(
    [os.path.join(ROOT, "run_app.py")], pathex=[ROOT], binaries=[], datas=datas,
    hiddenimports=collect_submodules("maple_flip_hunter") + ["PySide6.QtMultimedia", "PySide6.QtNetwork", "cv2", "numpy"],
    hookspath=[], runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="MapleFlipHunterFriends", debug=False, strip=False, upx=False,
          console=False, icon=None)
