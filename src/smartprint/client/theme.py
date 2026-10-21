"""Visual language taken from the project slides: blue/green/orange/red on a soft grey-blue canvas."""
from __future__ import annotations

NAVY = "#0B1F3A"
BLUE = "#286EDC"
GREEN = "#289B69"
ORANGE = "#EB872D"
RED = "#D74B4B"
BG = "#F7F9FC"
CARD = "#FFFFFF"
INK = "#14233B"
MUTED = "#6B7A90"
LINE = "#E2E8F1"

STATE_COLOR = {
    "DRAFT": MUTED, "CONFIGURED": BLUE, "AWAITING_PAYMENT": ORANGE, "PAID": GREEN, "QUEUED": BLUE,
    "PRINTING": BLUE, "AWAITING_STAFF": ORANGE, "COMPLETED": GREEN, "FAILED": RED, "UNCERTAIN": RED,
    "CANCELLED": MUTED, "EXPIRED": MUTED, "REFUND_PENDING": ORANGE, "REFUNDED": MUTED,
}

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", sans-serif; color: {INK}; }}
QMainWindow, QDialog, QWidget#page {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 30px; font-weight: 800; }}
QLabel#h2 {{ font-size: 20px; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; font-size: 13px; }}
QLabel#eyebrow {{ color: {BLUE}; font-size: 12px; font-weight: 700; letter-spacing: 2px; }}
QLabel#big {{ font-size: 44px; font-weight: 800; }}
QFrame#card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 16px; }}
QFrame#cardBlue {{ background: {BLUE}; border-radius: 16px; }}
QFrame#header {{ background: {NAVY}; }}
QFrame#header QLabel {{ color: white; }}
QPushButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 10px 18px;
               font-size: 14px; font-weight: 600; }}
QPushButton:hover {{ border-color: {BLUE}; }}
QPushButton:disabled {{ color: #A9B4C3; background: #F0F3F8; }}
QPushButton#primary {{ background: {BLUE}; color: white; border: none; padding: 14px 28px; font-size: 16px; }}
QPushButton#primary:hover {{ background: #1F5CC0; }}
QPushButton#primary:disabled {{ background: #B7C9EA; color: white; }}
QPushButton#success {{ background: {GREEN}; color: white; border: none; }}
QPushButton#danger {{ color: {RED}; border-color: #F0C9C9; }}
QPushButton#ghost {{ border: none; background: transparent; color: {MUTED}; }}
QPushButton#seg {{ border-radius: 10px; padding: 12px 10px; font-size: 14px; }}
QPushButton#seg:checked {{ background: {BLUE}; color: white; border-color: {BLUE}; }}
QSpinBox, QLineEdit, QComboBox, QDoubleSpinBox {{ background: white; border: 1px solid {LINE}; border-radius: 10px;
               padding: 9px 12px; font-size: 15px; min-height: 22px; }}
QSpinBox:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {BLUE}; }}
QTableWidget {{ background: white; border: 1px solid {LINE}; border-radius: 12px; gridline-color: transparent;
               selection-background-color: #E7F0FD; selection-color: {INK}; font-size: 13px; }}
QTableWidget::item {{ padding: 6px; border-bottom: 1px solid #EEF2F8; }}
QHeaderView::section {{ background: #F1F5FA; border: none; padding: 8px; font-weight: 700; color: {MUTED}; font-size: 12px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 12px 22px; font-weight: 700; color: {MUTED}; font-size: 14px; }}
QTabBar::tab:selected {{ color: {BLUE}; border-bottom: 3px solid {BLUE}; }}
QProgressBar {{ background: #E8EEF7; border: none; border-radius: 6px; height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: {BLUE}; border-radius: 6px; }}
QScrollBar:vertical {{ width: 10px; background: transparent; }}
QScrollBar::handle:vertical {{ background: #C9D3E2; border-radius: 5px; min-height: 30px; }}
QToolTip {{ background: {NAVY}; color: white; border: none; padding: 6px; }}
"""
