"""Dark theme. Generous padding everywhere: text never touches a border."""

BG = "#0f1419"
PANEL = "#171d25"
PANEL2 = "#1e2631"
BORDER = "#2a3442"
TEXT = "#e6edf3"
MUTED = "#8b98a8"
ACCENT = "#f5a524"      # maple orange
GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"
BLUE = "#58a6ff"

QSS = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI', 'Inter', Arial, sans-serif; font-size: 13px; }}
QMainWindow, QDialog {{ background: {BG}; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#title {{ font-size: 22px; font-weight: 800; color: {ACCENT}; letter-spacing: 2px; padding: 6px 12px; }}
QLabel#subtitle {{ color: {MUTED}; padding: 0 12px 6px 12px; }}
QLabel#pill {{ border-radius: 12px; padding: 6px 16px; font-weight: 700; }}
QLabel#stat {{ color: {MUTED}; padding: 6px 12px; }}
QLabel#statValue {{ color: {TEXT}; font-weight: 700; font-size: 15px; padding: 2px 12px 8px 12px; }}
QLabel#fieldLabel {{ color: {MUTED}; font-weight: 600; padding-left: 4px; }}
QSpinBox {{ padding: 6px 8px; border: 1px solid {BORDER}; border-radius: 6px; background: {PANEL}; }}
QLabel#section {{ color: {MUTED}; font-weight: 700; letter-spacing: 1px; padding: 10px 12px 4px 12px; }}
QPushButton {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 16px; min-height: 18px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton#primary {{ background: {ACCENT}; color: #1a1200; font-weight: 700; border: none; }}
QPushButton#primary:hover {{ background: #ffb73d; }}
QPushButton#danger {{ background: #3a1d1d; border-color: {RED}; }}
QCheckBox {{ padding: 6px 4px; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER}; background: {PANEL2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {{ background: {PANEL2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 10px; min-height: 18px; }}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 6px; background: {PANEL2}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -6px 0; border-radius: 8px; background: {ACCENT}; }}
QTableView {{ background: {PANEL}; alternate-background-color: {PANEL2}; gridline-color: {BORDER}; border: 1px solid {BORDER}; border-radius: 10px; selection-background-color: #2b3a52; selection-color: {TEXT}; }}
QTableView::item {{ padding: 8px 12px; }}
QHeaderView::section {{ background: {PANEL2}; color: {MUTED}; padding: 10px 12px; border: none; border-bottom: 1px solid {BORDER}; font-weight: 700; }}
QTextBrowser {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; padding: 14px; }}
QScrollBar:vertical {{ background: {BG}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 6px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {PANEL2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 8px 10px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ background: {PANEL2}; padding: 10px 18px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 4px; color: {MUTED}; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT}; }}
QStatusBar {{ background: {PANEL}; color: {MUTED}; padding: 4px 10px; border-top: 1px solid {BORDER}; }}
QMenu {{ background: {PANEL2}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu::item {{ padding: 8px 24px; border-radius: 6px; }}
QMenu::item:selected {{ background: {BORDER}; }}
QLabel#banner {{ background: #3a1414; color: #ffd166; border: 2px solid {RED}; border-radius: 12px; padding: 18px 24px; font-size: 16px; font-weight: 800; }}
"""

LEVEL_COLORS = {"JACKPOT": RED, "GOOD FLIP": GREEN, "UNCERTAIN": YELLOW, "NONE": MUTED}
STATE_COLORS = {"LIVE": GREEN, "DEGRADED": YELLOW, "OFFLINE": RED, "ERROR": RED, "STOPPED": MUTED, "STARTING": YELLOW}
