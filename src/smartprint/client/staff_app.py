"""SmartPrint staff console: queue control, xerox desk, refunds, pricing, audit."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

from . import theme
from .api import StaffApi
from .asyncutil import run_async
from .settings import ClientSettings, load_settings
from .views.staff_pages import AdminPage, AuditPage, DashboardPage, QueuePage
from .widgets.common import label


class LoginDialog(QDialog):
    def __init__(self, api: StaffApi):
        super().__init__()
        self.api = api
        self.setWindowTitle("SmartPrint — Staff sign in")
        self.setFixedWidth(400)
        lay = QVBoxLayout(self); lay.setContentsMargins(32, 28, 32, 28); lay.setSpacing(12)
        lay.addWidget(label("STAFF CONSOLE", "eyebrow"))
        lay.addWidget(label("Sign in", "h1"))
        self.user = QLineEdit(); self.user.setPlaceholderText("Username")
        self.pw = QLineEdit(); self.pw.setPlaceholderText("Password"); self.pw.setEchoMode(QLineEdit.Password)
        self.err = label("", wrap=True); self.err.setStyleSheet(f"color:{theme.RED}; font-weight:600;")
        self.btn = QPushButton("Sign in"); self.btn.setObjectName("primary"); self.btn.setDefault(True)
        for w in (self.user, self.pw, self.err, self.btn):
            lay.addWidget(w)
        self.btn.clicked.connect(self._go)
        self.pw.returnPressed.connect(self._go)

    def _go(self) -> None:
        self.btn.setEnabled(False); self.err.clear()

        def ok(_) -> None:
            self.accept()

        def fail(e: Exception) -> None:
            self.btn.setEnabled(True); self.err.setText(getattr(e, "detail", str(e)))
        run_async(lambda: self.api.login(self.user.text().strip(), self.pw.text()), ok, fail)


class StaffWindow(QMainWindow):
    def __init__(self, api: StaffApi):
        super().__init__()
        self.api = api
        self.setWindowTitle("SmartPrint — Staff Console")
        self.resize(1380, 860)
        root = QWidget(); root.setObjectName("page"); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        header = QFrame(); header.setObjectName("header"); header.setFixedHeight(60)
        h = QHBoxLayout(header); h.setContentsMargins(28, 0, 28, 0)
        logo = QLabel("SMART<span style='color:#6FA3F5'>PRINT</span> &nbsp;<span style='font-weight:400;font-size:13px;color:#9FB3D1'>Staff Console</span>")
        logo.setStyleSheet("font-size:21px; font-weight:800; letter-spacing:3px; color:white;")
        h.addWidget(logo); h.addStretch(1)
        h.addWidget(QLabel(f"{api.username}  ·  {api.role.title()}"))
        outer.addWidget(header)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        self.dash, self.queue, self.audit = DashboardPage(api), QueuePage(api), AuditPage(api)
        self.tabs.addTab(self.dash, "Dashboard"); self.tabs.addTab(self.queue, "Queue && Xerox desk"); self.tabs.addTab(self.audit, "Audit")
        self.admin = None
        if api.role == "ADMIN":
            self.admin = AdminPage(api); self.tabs.addTab(self.admin, "Admin")
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        self.timer = QTimer(self, interval=2000); self.timer.timeout.connect(self.refresh); self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        w = self.tabs.currentWidget()
        if w is self.admin and self.admin is not None:
            if not getattr(self, "_admin_loaded", False):
                self._admin_loaded = True; self.admin.refresh()
            return
        w.refresh()


def main(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(theme.QSS)
    api = StaffApi(load_settings())
    dlg = LoginDialog(api)
    if dlg.exec() != QDialog.Accepted:
        return 0
    win = StaffWindow(api)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
