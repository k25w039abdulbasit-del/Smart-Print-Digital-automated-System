"""SmartPrint customer kiosk (runs on every shop PC)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import theme
from .api import ApiError, KioskApi
from .asyncutil import run_async
from .settings import ClientSettings, load_settings
from .views.kiosk_pages import HomePage, PayPage, PrintPage, SettingsPage
from .widgets.common import Stepper, label

HOME, FILE_BUSY, SETTINGS, PAY, PRINT = range(5)


class KioskWindow(QMainWindow):
    def __init__(self, cfg: ClientSettings, dev_mode: bool = True):
        super().__init__()
        self.cfg = cfg
        self.api = KioskApi(cfg)
        self.setWindowTitle("SmartPrint")
        self.resize(1240, 800)
        root = QWidget(); root.setObjectName("page"); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        header = QFrame(); header.setObjectName("header"); header.setFixedHeight(64)
        h = QHBoxLayout(header); h.setContentsMargins(32, 0, 32, 0)
        logo = QLabel("SMART<span style='color:#6FA3F5'>PRINT</span>")
        logo.setStyleSheet("font-size:22px; font-weight:800; letter-spacing:3px; color:white;")
        h.addWidget(logo)
        h.addWidget(label("Smart Digital Printing & Shop Automation", "muted"))
        h.addStretch(1)
        self.conn = QLabel("● connecting…"); self.conn.setStyleSheet("color:#9FB3D1; font-weight:600;")
        h.addWidget(self.conn)
        h.addSpacing(16)
        h.addWidget(QLabel(f"<b>{cfg.pc_name}</b>"))
        outer.addWidget(header)

        self.stepper = Stepper()
        sw = QWidget(); sw.setObjectName("page"); sl = QHBoxLayout(sw); sl.setContentsMargins(220, 8, 220, 0)
        sl.addWidget(self.stepper)
        outer.addWidget(sw)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.home = HomePage(self.api)
        self.busy = self._busy_page()
        self.settings = SettingsPage(self.api)
        self.pay = PayPage(self.api, dev_mode=dev_mode)
        self.printp = PrintPage(self.api)
        for w in (self.home, self.busy, self.settings, self.pay, self.printp):
            self.stack.addWidget(w)

        self.toast = QLabel(""); self.toast.setAlignment(Qt.AlignCenter); self.toast.hide()
        self.toast.setStyleSheet(f"background:{theme.RED}; color:white; font-weight:700; padding:10px;")
        outer.addWidget(self.toast)

        self.home.digital.connect(self.start_digital)
        self.home.xerox.connect(self.start_xerox)
        self.settings.proceed.connect(lambda: self.go(PAY))
        self.settings.back.connect(self.cancel_and_home)
        self.pay.back.connect(lambda: self.go(SETTINGS))
        self.pay.cancelled.connect(lambda: self.go(HOME))
        self.pay.paid.connect(lambda j: (self.printp.start(j), self.go(PRINT)))
        self.printp.finished.connect(lambda: self.go(HOME))

        self.health = QTimer(self, interval=5000); self.health.timeout.connect(self.check_health)
        self.health.start()
        self.go(HOME)
        self.check_health()

    def _busy_page(self) -> QWidget:
        w = QWidget(); w.setObjectName("page")
        l = QVBoxLayout(w); l.setAlignment(Qt.AlignCenter)
        self.busy_label = label("Checking your file…", "h2", align=Qt.AlignCenter)
        l.addWidget(self.busy_label)
        l.addWidget(label("Validating, converting and counting pages", "muted", align=Qt.AlignCenter))
        return w

    # -- navigation
    def go(self, page: int, enter: bool = True) -> None:
        self.stepper.set_step({HOME: 0, FILE_BUSY: 0, SETTINGS: 1, PAY: 2, PRINT: 3}[page])
        if page == HOME:
            self.printp.stop()
            self.home.load_rates()
        elif page == PAY and enter:
            self.pay.start(self.settings.job)
        self.stack.setCurrentIndex(page)

    def error(self, e: Exception) -> None:
        self.toast.setText(getattr(e, "detail", None) or str(e))
        self.toast.show()
        QTimer.singleShot(6000, self.toast.hide)

    def start_digital(self, path: Path) -> None:
        self.busy_label.setText(f"Checking “{path.name}”…")
        self.go(FILE_BUSY)

        def ok(job: dict) -> None:
            self.settings.start(job)
            self.go(SETTINGS)

        def fail(e: Exception) -> None:
            self.go(HOME)
            self.error(e)
        run_async(lambda: self.api.upload(path), ok, fail)

    def start_xerox(self) -> None:
        def ok(job: dict) -> None:
            self.settings.start(job)
            self.go(SETTINGS)
        run_async(self.api.new_xerox, ok, self.error)

    def cancel_and_home(self) -> None:
        jid = self.settings.job.get("id")
        if jid:
            run_async(lambda: self.api.cancel(jid), None, lambda e: None)
        self.go(HOME)

    def check_health(self) -> None:
        def ok(h: dict) -> None:
            good = h.get("status") == "ok"
            self.conn.setText("● online" if good else "● printer issue")
            self.conn.setStyleSheet(f"color:{'#5FD6A2' if good else '#F5B06B'}; font-weight:600;")
        run_async(self.api.health, ok, lambda e: (self.conn.setText("● server offline"),
                                                  self.conn.setStyleSheet("color:#FF8A8A; font-weight:600;")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("smartprint-kiosk")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--no-dev-tools", action="store_true", help="hide the demo 'simulate payment' button")
    args = ap.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(theme.QSS)
    cfg = load_settings()
    w = KioskWindow(cfg, dev_mode=not args.no_dev_tools)
    w.showFullScreen() if args.fullscreen else w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
