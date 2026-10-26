"""Friends Edition lock screen (shown when the trial expired or the licence cannot be validated)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ..licensing import LicenseClient
from .theme import ACCENT, GREEN, MUTED, QSS, RED

CONTACT_EMAIL = "davidcohen2804@gmail.com"


class LockDialog(QDialog):
    def __init__(self, client: LicenseClient, reason: str = "", parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.unlocked = False
        self.setWindowTitle("Maple Flip Hunter — Friends Edition")
        self.setStyleSheet(QSS)
        self.setMinimumWidth(520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 28, 32, 28)
        v.setSpacing(14)
        t = QLabel("Your 3-day free trial has expired.")
        t.setObjectName("title")
        v.addWidget(t)
        body = QLabel("To continue using Maple Flip Hunter, please contact:")
        v.addWidget(body)
        row = QHBoxLayout()
        mail = QLabel(f"<b style='font-size:15px;color:{ACCENT}'>{CONTACT_EMAIL}</b>")
        mail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(mail)
        self.btn_copy = QPushButton("📋 Copy Email")
        self.btn_copy.clicked.connect(self.copy_email)
        row.addWidget(self.btn_copy)
        row.addStretch(1)
        v.addLayout(row)
        self.reason = QLabel(reason)
        self.reason.setObjectName("subtitle")
        self.reason.setWordWrap(True)
        v.addWidget(self.reason)
        v.addSpacing(6)
        v.addWidget(QLabel("Have an activation code?"))
        row2 = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self.code.returnPressed.connect(self.activate)
        row2.addWidget(self.code, 1)
        self.btn_activate = QPushButton("Activate")
        self.btn_activate.setObjectName("primary")
        self.btn_activate.clicked.connect(self.activate)
        row2.addWidget(self.btn_activate)
        v.addLayout(row2)
        self.result_lbl = QLabel("")
        self.result_lbl.setWordWrap(True)
        v.addWidget(self.result_lbl)
        row3 = QHBoxLayout()
        self.btn_retry = QPushButton("Check again")
        self.btn_retry.clicked.connect(self.retry)
        self.btn_quit = QPushButton("Quit")
        self.btn_quit.clicked.connect(self.reject)
        row3.addWidget(self.btn_retry)
        row3.addStretch(1)
        row3.addWidget(self.btn_quit)
        v.addLayout(row3)

    def copy_email(self) -> None:
        QApplication.clipboard().setText(CONTACT_EMAIL)
        self.btn_copy.setText("✓ Copied")

    def _finish_if_ok(self) -> bool:
        st = self.client.state()
        if st.status in ("trial", "licensed"):
            self.unlocked = True
            self.accept()
            return True
        return False

    def activate(self) -> None:
        code = self.code.text().strip()
        if not code:
            self.result_lbl.setText(f"<span style='color:{MUTED}'>Enter the code you received.</span>")
            return
        ok, msg = self.client.redeem(code)
        if ok and self._finish_if_ok():
            return
        self.result_lbl.setText(f"<span style='color:{RED}'>Activation failed: {msg}</span>")

    def retry(self) -> None:
        self.client.refresh() or self.client.activate()
        if not self._finish_if_ok():
            st = self.client.state()
            self.result_lbl.setText(f"<span style='color:{MUTED}'>{st.message}{(' — ' + self.client.last_error) if self.client.last_error else ''}</span>")


def unlocked_color(status: str) -> str:
    return GREEN if status in ("trial", "licensed") else RED
