"""Settings dialog: profit-band editor round-trips, refuses overlaps, previews, volume."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from maple_flip_hunter.config import Settings, SoundTier  # noqa: E402
from maple_flip_hunter.ui.settings_dialog import NO_MAX, SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeNotifier:
    def __init__(self):
        self.played = []
        self.volume = 0.9

    def play(self, sid, volume=None):
        self.played.append((sid, volume))

    def set_volume(self, v):
        self.volume = v


def test_dialog_roundtrip_add_remove_preview_volume(app):
    s = Settings()
    fn = FakeNotifier()
    dlg = SettingsDialog(s, notifier=fn)
    assert len(dlg.tier_rows) == 3
    assert dlg.tier_rows[2].hi.value() == NO_MAX            # unlimited upper bound shown as max
    dlg.tier_rows[0].sound.setCurrentIndex(dlg.tier_rows[0].sound.findData("chime"))
    dlg.tier_rows[1].enabled.setChecked(False)
    dlg.volume.setValue(40)
    dlg.tier_rows[0].preview.click()
    assert fn.played == [("chime", 0.4)]
    dlg._add_tier_clicked()
    assert len(dlg.tier_rows) == 4
    dlg._remove_tier(dlg.tier_rows[3])
    assert len(dlg.tier_rows) == 3
    out = dlg.result_settings(s)
    assert out.alert_volume == 0.4
    assert [t.sound for t in out.sound_tiers] == ["chime", "melody", "jackpot"]
    assert out.sound_tiers[1].enabled is False and out.sound_tiers[2].max_profit is None
    assert out.sound_tiers[0].min_profit == 10_000 and out.sound_tiers[0].max_profit == 19_999


def test_dialog_refuses_overlapping_bands(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    dlg = SettingsDialog(Settings(), notifier=FakeNotifier())
    dlg.tier_rows[0].hi.setValue(25_000)                      # now overlaps the 20k band
    dlg.accept()
    assert warned and dlg.result() != dlg.DialogCode.Accepted
    dlg.tier_rows[0].hi.setValue(19_999)
    dlg.accept()
    assert dlg.result() == dlg.DialogCode.Accepted


def test_extra_band_persists(app, tmp_path):
    s = Settings(sound_tiers=[SoundTier(min_profit=10_000, max_profit=14_999, sound="soft"),
                              SoundTier(min_profit=15_000, max_profit=29_999, sound="bell"),
                              SoundTier(min_profit=30_000, max_profit=99_999, sound="melody"),
                              SoundTier(min_profit=100_000, sound="jackpot")])
    s.save(tmp_path / "s.json")
    dlg = SettingsDialog(Settings.load(tmp_path / "s.json"), notifier=FakeNotifier())
    assert [r.to_tier().sound for r in dlg.tier_rows] == ["soft", "bell", "melody", "jackpot"]
