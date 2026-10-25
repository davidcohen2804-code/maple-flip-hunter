"""Audible + visual alerts. Audio backends, in order of preference:
  1. Qt Multimedia QSoundEffect (volume control, works inside the GUI process)
  2. winsound (Windows, no volume control)
  3. silent (logs only) - used in tests / headless
Visual toasts are delegated to a callback (the UI registers the system-tray notification)."""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from ..models import Opportunity
from .sounds import ensure_sounds

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, volume: float = 0.9, enable_audio: bool = True, sounds: Optional[dict[str, Path]] = None):
        self.volume = volume
        self.enable_audio = enable_audio
        self.sounds = sounds or ensure_sounds()
        self.toast_callback: Optional[Callable[[Opportunity, str, str, str], None]] = None
        self.history: list[tuple[float, str, str, str]] = []
        self._effects: dict[str, object] = {}
        self._backend = "silent"
        self._lock = threading.Lock()
        self.last_sound: Optional[str] = None
        if enable_audio:
            self._init_backend()

    def _init_backend(self) -> None:
        try:
            from PySide6.QtCore import QUrl  # noqa: F401
            from PySide6.QtMultimedia import QSoundEffect  # noqa: F401

            self._backend = "qt"
        except Exception:  # noqa: BLE001
            self._backend = "winsound" if sys.platform == "win32" else "silent"
        log.info("audio backend: %s", self._backend)

    @property
    def backend(self) -> str:
        return self._backend

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        for eff in self._effects.values():
            try:
                eff.setVolume(self.volume)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ playback
    def play(self, level: str, volume: Optional[float] = None) -> None:
        """Play a sound by id ("bell", "melody", "jackpot", ...) or by legacy level name ("JACKPOT", ...)."""
        path = self.sounds.get(level) or self.sounds.get("GOOD FLIP")
        if not path or not self.enable_audio:
            return
        if volume is not None:
            self.set_volume(volume)
        if self._backend == "qt":
            self._play_qt(level, path)
        elif self._backend == "winsound":
            try:
                import winsound

                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception as exc:  # noqa: BLE001
                log.warning("winsound failed: %s", exc)
        else:
            log.info("[silent audio] %s", level)

    def _play_qt(self, level: str, path: Path) -> None:
        try:
            from PySide6.QtCore import QCoreApplication, QUrl
            from PySide6.QtMultimedia import QSoundEffect

            if QCoreApplication.instance() is None:
                raise RuntimeError("no Qt application")
            eff = self._effects.get(level)
            if eff is None:
                eff = QSoundEffect()
                eff.setSource(QUrl.fromLocalFile(str(path)))
                self._effects[level] = eff
            eff.setVolume(self.volume)  # type: ignore[attr-defined]
            eff.play()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.warning("Qt audio failed (%s); falling back", exc)
            self._backend = "winsound" if sys.platform == "win32" else "silent"
            self.play(level)

    # --------------------------------------------------------------- alert
    def alert(self, opp: Opportunity, level: str, title: str, body: str, sound: Optional[str] = "__level__") -> None:
        """`sound` = sound id chosen by the profit tier; None = silent alert (toast only);
        the default sentinel keeps the old behaviour of playing the level's default sound."""
        import time

        with self._lock:
            self.history.append((time.time(), level, title, body))
            if len(self.history) > 500:
                del self.history[:100]
        self.last_sound = level if sound == "__level__" else sound
        if self.last_sound is not None:
            self.play(self.last_sound)
        if self.toast_callback is not None:
            try:
                self.toast_callback(opp, level, title, body)
            except Exception:  # noqa: BLE001
                log.exception("toast callback failed")
