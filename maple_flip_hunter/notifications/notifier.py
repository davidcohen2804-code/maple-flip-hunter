"""Audible + visual alerts. Audio backends, in order of preference:
  1. Qt Multimedia QSoundEffect (volume control, works inside the GUI process)
  2. winsound (Windows, no volume control)
  3. silent (logs only) - used in tests / headless

THREADING (the cause of "Test alarm works but automatic alerts are silent"): alerts are raised on the scanner's
asyncio thread, while QSoundEffect only works from a thread that runs a Qt event loop. Every play() request is
therefore marshalled to the GUI thread through a queued Qt signal (`_AudioProxy`); the QSoundEffect objects are
created once on the GUI thread, parented to the proxy and kept for the life of the process.

Log lines (grep them in %APPDATA%\\MapleFlipHunter\\logs): ALERT_CREATED, AUDIO_REQUESTED, AUDIO_STARTED,
AUDIO_ERROR, AUDIO_SUPPRESSED reason=…
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..models import Opportunity
from .sounds import ensure_sounds

log = logging.getLogger(__name__)


def _qt_proxy_class():
    """Built lazily so the module imports without PySide6 (headless / tests without Qt)."""
    from PySide6.QtCore import QObject, Signal, Slot

    class _AudioProxy(QObject):
        request = Signal(str, float)      # sound id, volume  — queued across threads

        def __init__(self, owner: "Notifier") -> None:
            super().__init__()
            self.owner = owner
            self.request.connect(self._on_request)   # AutoConnection: queued when emitted from another thread

        @Slot(str, float)
        def _on_request(self, sound_id: str, volume: float) -> None:
            self.owner._play_qt_gui_thread(sound_id, volume)

    return _AudioProxy


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
        self.last_play_at: Optional[float] = None      # wall clock of the last play() that reached a backend
        self.last_started_at: Optional[float] = None   # wall clock of the last AUDIO_STARTED
        self.sound_latency_ms: list[float] = []         # listing first seen locally -> sound handed to the backend
        self.muted_until = 0.0
        self.audio_log: list[tuple[float, str, str]] = []   # (time, event, detail) — shown by the UI diagnostics
        self._proxy = None
        self._gui_thread = None
        if enable_audio:
            self._init_backend()

    # ------------------------------------------------------------ backend
    def _init_backend(self) -> None:
        if sys.platform not in ("win32", "darwin"):
            # Qt Multimedia probes system audio on construction and can crash a Linux box without a sound
            # server (CI containers); the product targets Windows, so Linux is log-only.
            self._backend = "silent"
            log.info("audio backend: silent (non-Windows platform)")
            return
        try:
            from PySide6.QtCore import QCoreApplication, QThread  # noqa: F401
            from PySide6.QtMultimedia import QSoundEffect  # noqa: F401

            self._backend = "qt"
            app = QCoreApplication.instance()
            if app is not None:
                self._ensure_proxy()
        except Exception:  # noqa: BLE001
            self._backend = "winsound" if sys.platform == "win32" else "silent"
        log.info("audio backend: %s", self._backend)

    def _ensure_proxy(self) -> bool:
        """Create the GUI-thread proxy (must be called on the GUI thread, once). Returns True when ready."""
        if self._proxy is not None:
            return True
        try:
            from PySide6.QtCore import QCoreApplication, QThread

            app = QCoreApplication.instance()
            if app is None or QThread.currentThread() is not app.thread():
                return False
            if not self.audio_outputs():
                # creating a QSoundEffect with no output device can crash the process on some platforms
                self._backend = "winsound" if sys.platform == "win32" else "silent"
                self._log("AUDIO_ERROR", f"no audio output device found; Qt audio disabled, backend={self._backend}")
                return False
            self._proxy = _qt_proxy_class()(self)
            self._gui_thread = app.thread()
            self._preload_effects()
            return True
        except Exception as exc:  # noqa: BLE001
            self._log("AUDIO_ERROR", f"proxy init failed: {exc}")
            return False

    def _preload_effects(self) -> None:
        """Create every QSoundEffect once on the GUI thread so nothing is constructed (or destroyed) at alert time."""
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect

        for sid, path in self.sounds.items():
            if sid in self._effects:
                continue
            eff = QSoundEffect(self._proxy)                  # parented: lives as long as the proxy
            eff.setSource(QUrl.fromLocalFile(str(path)))
            eff.setVolume(self.volume)
            self._effects[sid] = eff

    @staticmethod
    def audio_outputs() -> list[str]:
        try:
            from PySide6.QtMultimedia import QMediaDevices

            return [d.description() for d in QMediaDevices.audioOutputs()]
        except Exception:  # noqa: BLE001
            return []

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def on_gui_thread(self) -> bool:
        try:
            from PySide6.QtCore import QCoreApplication, QThread

            app = QCoreApplication.instance()
            return app is not None and QThread.currentThread() is app.thread()
        except Exception:  # noqa: BLE001
            return True

    def _log(self, event: str, detail: str) -> None:
        self.audio_log.append((time.time(), event, detail))
        del self.audio_log[:-200]
        (log.warning if event == "AUDIO_ERROR" else log.info)("%s %s", event, detail)

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        for eff in self._effects.values():
            try:
                eff.setVolume(self.volume)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ playback
    def play(self, level: str, volume: Optional[float] = None) -> None:
        """Play a sound by id ("bell", "melody", "jackpot", ...) or by legacy level name ("JACKPOT", ...).
        Safe to call from any thread."""
        path = self.sounds.get(level)
        if path is None:
            self._log("AUDIO_ERROR", f"unknown sound id {level!r}; playing the default bell instead")
            level, path = "bell", self.sounds.get("bell") or self.sounds.get("GOOD FLIP")
        if not path:
            self._log("AUDIO_SUPPRESSED", "reason=no sound files available")
            return
        if volume is not None:
            self.set_volume(volume)
        self._log("AUDIO_REQUESTED", f"sound={level} backend={self._backend} volume={self.volume:.2f} "
                                     f"thread={'gui' if self.on_gui_thread else 'worker'}")
        if time.time() < self.muted_until:
            self._log("AUDIO_SUPPRESSED", f"reason=muted for {self.muted_until - time.time():.0f}s more")
            return
        self.last_play_at = time.time()      # the moment the sound is handed to the backend (or logged when audio is off)
        if not self.enable_audio:
            self._log("AUDIO_SUPPRESSED", "reason=audio disabled (notifier)")
            return
        if self._backend == "qt":
            self._play_qt(level, path)
        elif self._backend == "winsound":
            self._play_winsound(level, path)
        else:
            self._log("AUDIO_SUPPRESSED", "reason=silent backend (no Qt Multimedia, not Windows)")

    def _play_winsound(self, level: str, path: Path) -> None:
        try:
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            self.last_started_at = time.time()
            self._log("AUDIO_STARTED", f"sound={level} backend=winsound file={path.name}")
        except Exception as exc:  # noqa: BLE001
            self._log("AUDIO_ERROR", f"winsound failed: {exc}")

    def _play_qt(self, level: str, path: Path) -> None:
        """Dispatch to the GUI thread. If no Qt application exists (headless), use winsound/silent instead."""
        try:
            from PySide6.QtCore import QCoreApplication

            if QCoreApplication.instance() is None:
                raise RuntimeError("no Qt application")
        except Exception as exc:  # noqa: BLE001
            self._backend = "winsound" if sys.platform == "win32" else "silent"
            self._log("AUDIO_ERROR", f"Qt audio unavailable ({exc}); falling back to {self._backend}")
            self.play(level)
            return
        if self._proxy is None and not self._ensure_proxy():
            # called from a worker thread before the GUI created the proxy: nothing can run on the GUI thread yet
            self._log("AUDIO_ERROR", "GUI audio proxy not initialised (call Notifier.attach_gui() on the GUI thread)")
            self._play_winsound(level, path) if sys.platform == "win32" else None
            return
        if self.on_gui_thread:
            self._play_qt_gui_thread(level, self.volume)
        else:
            self._proxy.request.emit(level, self.volume)     # queued → runs _play_qt_gui_thread on the GUI thread

    def _play_qt_gui_thread(self, level: str, volume: float) -> None:
        try:
            eff = self._effects.get(level)
            if eff is None:
                self._preload_effects()
                eff = self._effects.get(level)
            if eff is None:
                raise RuntimeError(f"no effect for {level}")
            eff.setVolume(volume)  # type: ignore[attr-defined]
            eff.play()  # type: ignore[attr-defined]
            self.last_started_at = time.time()
            st = getattr(eff, "status", lambda: "?")()
            self._log("AUDIO_STARTED", f"sound={level} backend=qt status={st} playing={getattr(eff, 'isPlaying', lambda: '?')()} thread=gui")
        except Exception as exc:  # noqa: BLE001
            self._log("AUDIO_ERROR", f"Qt audio failed ({exc}); falling back")
            self._backend = "winsound" if sys.platform == "win32" else "silent"
            path = self.sounds.get(level)
            if path and self._backend == "winsound":
                self._play_winsound(level, path)

    def attach_gui(self) -> bool:
        """Call once from the GUI thread (MainWindow does) so worker-thread alerts can reach Qt audio."""
        return self._ensure_proxy() if self._backend == "qt" else True

    def stop_all(self) -> None:
        """Stop any sound that is still playing (Mute button)."""
        for eff in self._effects.values():
            try:
                eff.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        if self._backend == "winsound" and sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:  # noqa: BLE001
                pass

    def mute_for(self, seconds: float) -> None:
        self.stop_all()
        self.muted_until = time.time() + seconds
        self._log("AUDIO_SUPPRESSED", f"reason=user pressed Mute ({seconds:.0f}s)")

    # --------------------------------------------------------------- alert
    def alert(self, opp: Opportunity, level: str, title: str, body: str, sound: Optional[str] = "__level__") -> None:
        """`sound` = sound id chosen by the profit tier; None = silent alert (toast only);
        the default sentinel keeps the old behaviour of playing the level's default sound."""
        with self._lock:
            self.history.append((time.time(), level, title, body))
            if len(self.history) > 500:
                del self.history[:100]
        self.last_sound = level if sound == "__level__" else sound
        self._log("ALERT_CREATED", f"{level} {opp.listing.item_name!r} buy={opp.listing.price:,} "
                                   f"net={opp.valuation.net_profit} sound={self.last_sound}")
        if self.last_sound is None:
            self._log("AUDIO_SUPPRESSED", "reason=profit band disabled or net profit outside every band")
        else:
            self.play(self.last_sound)
            if self.last_play_at is not None:
                self.sound_latency_ms.append((self.last_play_at - opp.listing.local_first_seen_at) * 1000)
                del self.sound_latency_ms[:-500]
        if self.toast_callback is not None:
            try:
                self.toast_callback(opp, level, title, body)
            except Exception:  # noqa: BLE001
                log.exception("toast callback failed")
