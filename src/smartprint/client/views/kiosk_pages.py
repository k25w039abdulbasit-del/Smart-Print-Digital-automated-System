"""The four customer screens of the kiosk."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from ...shared.enums import money
from .. import theme
from ..api import ApiError, KioskApi
from ..asyncutil import run_async
from ..widgets.common import NumberStepper, Pill, QRWidget, Segmented, card, label

SUPPORTED = "Documents (*.pdf *.png *.jpg *.jpeg *.txt *.docx *.doc *.pptx *.xlsx *.odt *.rtf);;All files (*)"


def page_widget() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("page")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(48, 28, 48, 28)
    lay.setSpacing(18)
    return w, lay


# ------------------------------------------------------------------ Home / FILE
class HomePage(QWidget):
    digital = Signal(Path)
    xerox = Signal()

    def __init__(self, api: KioskApi):
        super().__init__()
        self.api = api
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(48, 36, 48, 28)
        root.setSpacing(22)
        root.addWidget(label("WELCOME", "eyebrow"))
        root.addWidget(label("What would you like to do?", "h1"))
        root.addWidget(label("No account needed. Choose a service, pay, and collect your pages.", "muted"))

        row = QHBoxLayout()
        row.setSpacing(24)
        row.addWidget(self._service("🖨", "Digital Print", "Send a file from this PC or a USB drive.\nPDF, images, text and Office documents.",
                                    "Choose a file…", self._browse, theme.BLUE, "btnDigital"))
        row.addWidget(self._service("▤", "Xerox Copy", "Copy a paper document.\nPick options, pay, then hand your pages to staff.",
                                    "Start a copy job", self.xerox.emit, theme.ORANGE, "btnXerox"))
        root.addLayout(row, 1)

        self.rates = label("", "muted", align=Qt.AlignCenter)
        root.addWidget(self.rates)
        hint = label("Tip: you can also drag & drop a file anywhere on this screen.", "muted", align=Qt.AlignCenter)
        root.addWidget(hint)

    def _service(self, icon: str, title: str, text: str, btn: str, cb: Callable, color: str, obj: str) -> QFrame:
        c = card(margins=32, spacing=14)
        ic = QLabel(icon)
        ic.setStyleSheet(f"font-size:44px; color:{color};")
        c.layout().addWidget(ic)
        c.layout().addWidget(label(title, "h2"))
        c.layout().addWidget(label(text, "muted", wrap=True))
        c.layout().addStretch(1)
        b = QPushButton(btn)
        b.setObjectName("primary")
        b.setStyleSheet(f"background:{color};")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: cb())
        b.setProperty("testid", obj)
        setattr(self, obj, b)
        c.layout().addWidget(b)
        return c

    def load_rates(self) -> None:
        def fmt(d: dict) -> str:
            c = d["config"]
            cur, dig, xer = c["currency"], c["services"]["digital"]["A4"], c["services"]["xerox"]["A4"]
            return (f"A4 per side — print: B/W {money(dig['BW'], cur)} · Colour {money(dig['COLOR'], cur)}    |    "
                    f"copy: B/W {money(xer['BW'], cur)} · Colour {money(xer['COLOR'], cur)}")
        run_async(self.api.rate_card, lambda d: self.rates.setText(fmt(d)), lambda e: self.rates.setText(""))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file to print", str(Path.home()), SUPPORTED)
        if path:
            self.digital.emit(Path(path))

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.digital.emit(Path(urls[0]))


# ------------------------------------------------------------------ SETTINGS
class SettingsPage(QWidget):
    proceed = Signal()
    back = Signal()

    def __init__(self, api: KioskApi):
        super().__init__()
        self.api = api
        self.job: dict = {}
        self.page = 1
        self._seq = 0
        self._valid = False
        self.debounce = QTimer(self, singleShot=True, interval=350)
        self.debounce.timeout.connect(self._push)

        root = QHBoxLayout(self)
        root.setContentsMargins(48, 24, 48, 24)
        root.setSpacing(24)

        # left: document preview
        self.left = card(margins=18, spacing=10)
        self.fname = label("", "h2", wrap=True)
        self.finfo = label("", "muted")
        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setMinimumSize(300, 380)
        self.thumb.setStyleSheet(f"background:#EEF2F8; border-radius:10px;")
        nav = QHBoxLayout()
        self.prev, self.next = QPushButton("‹"), QPushButton("›")
        for b, d in ((self.prev, -1), (self.next, 1)):
            b.setFixedWidth(46)
            b.clicked.connect(lambda _=False, d=d: self._flip(d))
        self.pageno = label("", "muted", align=Qt.AlignCenter)
        nav.addWidget(self.prev); nav.addWidget(self.pageno, 1); nav.addWidget(self.next)
        self.left.layout().addWidget(self.fname)
        self.left.layout().addWidget(self.finfo)
        self.left.layout().addWidget(self.thumb, 1)
        self.left.layout().addLayout(nav)
        root.addWidget(self.left, 4)

        # middle: options
        mid = card(margins=24, spacing=14)
        mid.layout().addWidget(label("Print options", "h2"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        self.originals = NumberStepper(1, 2000, 1)
        self.copies = NumberStepper(1, 500, 1)
        self.color = Segmented([("Black & White", "BW"), ("Colour", "COLOR")])
        self.duplex = Segmented([("One-sided", False), ("Double-sided", True)])
        self.paper = QComboBox(); self.paper.addItem("A4", "A4"); self.paper.addItem("A3", "A3"); self.paper.addItem("Letter", "LETTER")
        self.range = QLineEdit(); self.range.setPlaceholderText("All pages  (e.g. 1-3, 5, 8-10)")
        rows = [("Original pages", self.originals), ("Copies", self.copies), ("Colour", self.color),
                ("Sides", self.duplex), ("Paper", self.paper), ("Pages", self.range)]
        self._rows: dict[str, tuple[QLabel, QWidget]] = {}
        for i, (name, w) in enumerate(rows):
            lb = label(name, "muted")
            grid.addWidget(lb, i, 0)
            grid.addWidget(w, i, 1)
            self._rows[name] = (lb, w)
        grid.setColumnStretch(1, 1)
        mid.layout().addLayout(grid)
        self.err = label("", wrap=True)
        self.err.setStyleSheet(f"color:{theme.RED}; font-weight:600;")
        mid.layout().addWidget(self.err)
        mid.layout().addStretch(1)
        root.addWidget(mid, 5)

        # right: live quote
        q = card(margins=24, spacing=10, name="cardBlue")
        q.setStyleSheet("QFrame#cardBlue{background:%s;border-radius:16px;} QLabel{color:white;}" % theme.NAVY)
        q.layout().addWidget(label("YOUR PRICE", "eyebrow"))
        self.total = label("—", "big")
        self.total.setStyleSheet("color:white; font-size:40px; font-weight:800;")
        q.layout().addWidget(self.total)
        self.lines = QVBoxLayout()
        self.lines.setSpacing(6)
        q.layout().addLayout(self.lines)
        q.layout().addStretch(1)
        self.note = label("Calculated by the server. The price you see is the price you pay.", wrap=True)
        self.note.setStyleSheet("color:#9FB3D1; font-size:12px;")
        q.layout().addWidget(self.note)
        self.go = QPushButton("Continue to payment  →")
        self.go.setObjectName("primary")
        self.go.setEnabled(False)
        self.go.clicked.connect(self.proceed.emit)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.setStyleSheet("color:#9FB3D1;")
        cancel.clicked.connect(self.back.emit)
        q.layout().addWidget(self.go)
        q.layout().addWidget(cancel)
        root.addWidget(q, 4)

        for w in (self.originals, self.copies):
            w.valueChanged.connect(self._changed)
        self.range.textChanged.connect(self._changed)
        self.paper.currentIndexChanged.connect(self._changed)
        self.color.changed.connect(self._changed)
        self.duplex.changed.connect(self._changed)

    # -- lifecycle
    def start(self, job: dict) -> None:
        self.job, self.page, self._valid = job, 1, False
        digital = job["kind"] == "DIGITAL"
        self.left.setVisible(digital)
        for name in ("Original pages",):
            lb, w = self._rows[name]; lb.setVisible(not digital); w.setVisible(not digital)
        lb, w = self._rows["Pages"]; lb.setVisible(digital); w.setVisible(digital)
        self.range.clear(); self.copies.setValue(1); self.originals.setValue(1)
        self.color.set_value("BW"); self.duplex.set_value(False); self.paper.setCurrentIndex(0)
        self.err.clear()
        self.go.setEnabled(False)
        self.total.setText("—")
        if digital:
            self.fname.setText(job["original_name"])
            self.finfo.setText(f"{job['page_count']} page(s) · {job['file_size'] / 1024:.0f} KB")
            self._load_preview()
        self._push()

    def _flip(self, d: int) -> None:
        n = self.job.get("page_count") or 1
        self.page = min(max(1, self.page + d), n)
        self._load_preview()

    def _load_preview(self) -> None:
        n = self.job["page_count"]
        self.pageno.setText(f"Page {self.page} of {n}")
        self.prev.setEnabled(self.page > 1); self.next.setEnabled(self.page < n)
        jid, page = self.job["id"], self.page

        def show(png: bytes) -> None:
            if self.job.get("id") != jid:
                return
            pm = QPixmap(); pm.loadFromData(png)
            self.thumb.setPixmap(pm.scaled(QSize(340, 440), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        run_async(lambda: self.api.preview(jid, page, 520), show, lambda e: self.thumb.setText("Preview unavailable"))

    def _changed(self, *_) -> None:
        self.go.setEnabled(False)
        self.debounce.start()

    def current_settings(self) -> dict:
        s = {"copies": self.copies.value(), "color": self.color.value(), "duplex": self.duplex.value(),
             "paper": self.paper.currentData(), "page_range": self.range.text().strip()}
        if self.job["kind"] == "XEROX":
            s["originals"] = self.originals.value(); s["page_range"] = ""
        return s

    def _push(self) -> None:
        self._seq += 1
        seq, jid, body = self._seq, self.job["id"], self.current_settings()

        def ok(job: dict) -> None:
            if seq != self._seq:
                return                       # stale response from an older edit
            self.job = {**self.job, **job}
            self._valid = True
            self.err.clear()
            self._render_quote(job)
            self.go.setEnabled(True)

        def fail(e: Exception) -> None:
            if seq != self._seq:
                return
            self._valid = False
            self.err.setText(getattr(e, "detail", str(e)))
            self.total.setText("—")
            self._clear_lines()
        run_async(lambda: self.api.set_settings(jid, body), ok, fail)

    def _clear_lines(self) -> None:
        while self.lines.count():
            it = self.lines.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _render_quote(self, job: dict) -> None:
        cur = job["currency"]
        self.total.setText(money(job["total_minor"], cur))
        self._clear_lines()
        for ln in job["quote"]["lines"]:
            row = QHBoxLayout()
            qty = f"{ln['qty']} × {money(ln['unit_minor'], cur)}  " if ln["qty"] > 1 else ""
            a = label(f"{ln['label']}", wrap=True); a.setStyleSheet("color:#C9D6EC; font-size:13px;")
            b = label(money(ln["amount_minor"], cur)); b.setStyleSheet("color:white; font-size:13px; font-weight:600;")
            b.setAlignment(Qt.AlignRight)
            if qty:
                a.setText(f"{ln['label']}\n{qty.strip()}")
            row.addWidget(a, 1); row.addWidget(b)
            wrap = QWidget(); wrap.setLayout(row)
            self.lines.addWidget(wrap)


# ------------------------------------------------------------------ PAY
class PayPage(QWidget):
    paid = Signal(dict)        # job is past payment (QUEUED / AWAITING_STAFF / ...)
    back = Signal()
    cancelled = Signal()

    def __init__(self, api: KioskApi, dev_mode: bool = True):
        super().__init__()
        self.api, self.dev_mode = api, dev_mode
        self.job: dict = {}
        self.method = "ONLINE"
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._poll)
        self._polling = False

        root = QHBoxLayout(self)
        root.setContentsMargins(48, 24, 48, 24)
        root.setSpacing(24)

        left = card(margins=28, spacing=12)
        left.layout().addWidget(label("TOTAL TO PAY", "eyebrow"))
        self.amount = label("", "big")
        left.layout().addWidget(self.amount)
        self.summary = label("", "muted", wrap=True)
        left.layout().addWidget(self.summary)
        self.method_sel = Segmented([("📱  Pay online", "ONLINE"), ("💵  Pay at counter", "COUNTER")])
        self.method_sel.changed.connect(self._method)
        left.layout().addSpacing(8)
        left.layout().addWidget(label("Payment method", "muted"))
        left.layout().addWidget(self.method_sel)
        left.layout().addStretch(1)
        self.expires = label("", "muted")
        left.layout().addWidget(self.expires)
        row = QHBoxLayout()
        self.edit = QPushButton("‹ Change options"); self.edit.clicked.connect(self._edit)
        self.cancel = QPushButton("Cancel job"); self.cancel.setObjectName("danger"); self.cancel.clicked.connect(self._cancel)
        row.addWidget(self.edit); row.addStretch(1); row.addWidget(self.cancel)
        left.layout().addLayout(row)
        root.addWidget(left, 5)

        right = card(margins=28, spacing=10)
        right.layout().setAlignment(Qt.AlignHCenter)
        self.title = label("", "h2", align=Qt.AlignCenter)
        right.layout().addWidget(self.title)
        self.qr = QRWidget(230)
        right.layout().addWidget(self.qr, 0, Qt.AlignHCenter)
        self.code = label("", align=Qt.AlignCenter)
        self.code.setStyleSheet(f"font-size:44px; font-weight:800; letter-spacing:6px; color:{theme.BLUE};")
        right.layout().addWidget(self.code)
        self.instr = label("", "muted", wrap=True, align=Qt.AlignCenter)
        right.layout().addWidget(self.instr)
        self.status = Pill("Waiting for payment…", theme.ORANGE)
        right.layout().addWidget(self.status, 0, Qt.AlignHCenter)
        self.bar = QProgressBar(); self.bar.setRange(0, 0); self.bar.setTextVisible(False); self.bar.setFixedWidth(260)
        right.layout().addWidget(self.bar, 0, Qt.AlignHCenter)
        self.sim = QPushButton("⚡ Demo: simulate customer paying")
        self.sim.setObjectName("success")
        self.sim.clicked.connect(self._simulate)
        right.layout().addWidget(self.sim, 0, Qt.AlignHCenter)
        self.msg = label("", wrap=True, align=Qt.AlignCenter)
        self.msg.setStyleSheet(f"color:{theme.RED}; font-weight:600;")
        right.layout().addWidget(self.msg)
        right.layout().addStretch(1)
        root.addWidget(right, 5)

    def start(self, job: dict) -> None:
        self.job = job
        cur = job["currency"]
        self.amount.setText(money(job["total_minor"], cur))
        s = job["settings"]
        kind = "Xerox copy" if job["kind"] == "XEROX" else job["original_name"]
        self.summary.setText(f"{kind}\n{job['quote']['sides']} side(s) · {s['copies']} {'copy' if s['copies'] == 1 else 'copies'} · "
                             f"{'Colour' if s['color'] == 'COLOR' else 'B/W'} · {s['paper']} · "
                             f"{'double' if s['duplex'] else 'single'}-sided")
        self.sim.setVisible(self.dev_mode)
        self.msg.clear()
        self._request(self.method_sel.value())

    def _method(self, m: str) -> None:
        self._request(m)

    def _request(self, method: str) -> None:
        self.method = method
        self.timer.stop()
        self.status.set("Preparing payment…", theme.MUTED)
        jid = self.job["id"]
        run_async(lambda: self.api.pay(jid, method, uuid.uuid4().hex), self._show_payment,
                  lambda e: self.msg.setText(getattr(e, "detail", str(e))))

    def _show_payment(self, job: dict) -> None:
        self.job = {**self.job, **job}
        p = job["payment"]
        self.msg.clear()
        if p["method"] == "ONLINE":
            self.title.setText("Scan with your wallet app")
            self.qr.setVisible(True); self.qr.set_data(p["pay_payload"])
            self.code.setText(p["reference"][-6:]); self.code.setStyleSheet(
                f"font-size:22px; font-weight:800; letter-spacing:4px; color:{theme.MUTED};")
            self.instr.setText(f"Reference {p['reference']}\nThe job starts printing automatically once your payment is verified.")
        else:
            self.title.setText("Pay at the counter")
            self.qr.setVisible(False)
            self.code.setText(job["code"]); self.code.setStyleSheet(
                f"font-size:52px; font-weight:800; letter-spacing:6px; color:{theme.BLUE};")
            self.instr.setText(f"Tell the staff this code and pay {money(job['total_minor'], job['currency'])}.\n"
                               "Printing starts as soon as they confirm your payment.")
        self.status.set("Waiting for payment…", theme.ORANGE)
        self.sim.setText("⚡ Demo: simulate customer paying" if p["method"] == "ONLINE" else "⚡ Demo: staff confirms cash")
        self.sim.setVisible(self.dev_mode and p["method"] == "ONLINE")
        self.timer.start()

    def _poll(self) -> None:
        if self._polling:
            return
        self._polling = True
        jid = self.job["id"]

        def ok(job: dict) -> None:
            self._polling = False
            if job["id"] != self.job.get("id"):
                return
            self.job = {**self.job, **job}
            st = job["state"]
            if st in ("QUEUED", "PRINTING", "AWAITING_STAFF", "COMPLETED", "PAID", "REFUND_PENDING"):
                self.timer.stop(); self.paid.emit(job)
            elif st == "CONFIGURED":
                self.timer.stop()
                self.status.set("Payment not completed", theme.RED)
                self.msg.setText("That payment request ended. Choose a method to try again.")
                self.sim.setVisible(False)
            elif job["payment"] and job["payment"]["state"] == "MISMATCH":
                self.status.set("Amount mismatch — see staff", theme.RED)
                self.msg.setText("The amount received differs from the price. Please ask the staff for help.")
            elif st in ("CANCELLED", "EXPIRED"):
                self.timer.stop(); self.cancelled.emit()

        def fail(e: Exception) -> None:
            self._polling = False
            self.status.set("Connection problem — retrying…", theme.RED)
        run_async(lambda: self.api.job(jid), ok, fail)

    def _simulate(self) -> None:
        ref = self.job["payment"]["reference"]
        run_async(lambda: self.api.simulate_payment(ref), lambda _: self._poll(),
                  lambda e: self.msg.setText(str(e)))

    def _edit(self) -> None:
        self.timer.stop()
        jid = self.job["id"]
        run_async(lambda: self.api.withdraw_payment(jid), lambda _: self.back.emit(),
                  lambda e: self.msg.setText(getattr(e, "detail", str(e))))

    def _cancel(self) -> None:
        self.timer.stop()
        jid = self.job["id"]
        run_async(lambda: self.api.cancel(jid), lambda _: self.cancelled.emit(), lambda e: self.cancelled.emit())


# ------------------------------------------------------------------ PRINT
class PrintPage(QWidget):
    finished = Signal()

    DIGITAL_STEPS = [("PAID", "Payment verified"), ("QUEUED", "In print queue"), ("PRINTING", "Printing"), ("COMPLETED", "Ready to collect")]
    XEROX_STEPS = [("PAID", "Payment verified"), ("AWAITING_STAFF", "Staff is copying your pages"), ("COMPLETED", "Ready to collect")]
    ORDER = ["PAID", "QUEUED", "AWAITING_STAFF", "PRINTING", "COMPLETED"]

    def __init__(self, api: KioskApi):
        super().__init__()
        self.api = api
        self.job: dict = {}
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._poll)
        self.reset_timer = QTimer(self, interval=1000)
        self.reset_timer.timeout.connect(self._tick_reset)
        self._left = 0
        self._polling = False

        root = QVBoxLayout(self)
        root.setContentsMargins(120, 28, 120, 28)
        c = card(margins=36, spacing=14)
        self.icon = label("🖨", align=Qt.AlignCenter); self.icon.setStyleSheet("font-size:60px;")
        c.layout().addWidget(self.icon)
        self.title = label("", "h1", align=Qt.AlignCenter)
        c.layout().addWidget(self.title)
        self.sub = label("", "muted", wrap=True, align=Qt.AlignCenter)
        c.layout().addWidget(self.sub)
        self.steps_box = QVBoxLayout(); self.steps_box.setSpacing(10)
        wrap = QWidget(); wrap.setLayout(self.steps_box); wrap.setMaximumWidth(420)
        c.layout().addWidget(wrap, 0, Qt.AlignHCenter)
        self.code = label("", align=Qt.AlignCenter)
        self.code.setStyleSheet(f"font-size:30px; font-weight:800; letter-spacing:5px; color:{theme.BLUE};")
        c.layout().addWidget(self.code)
        self.done = QPushButton("Start a new job"); self.done.setObjectName("primary"); self.done.clicked.connect(self.finished.emit)
        c.layout().addWidget(self.done, 0, Qt.AlignHCenter)
        root.addWidget(c, 1)
        self._step_labels: dict[str, QLabel] = {}

    def start(self, job: dict) -> None:
        self.job = job
        self.reset_timer.stop()
        self.done.setVisible(False)
        steps = self.XEROX_STEPS if job["kind"] == "XEROX" else self.DIGITAL_STEPS
        while self.steps_box.count():
            it = self.steps_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._step_labels = {}
        for key, text in steps:
            lb = label(text); self._step_labels[key] = lb; self.steps_box.addWidget(lb)
        self.code.setText(job["code"])
        self._render(job)
        self.timer.start()

    def _poll(self) -> None:
        if self._polling:
            return
        self._polling = True
        jid = self.job["id"]

        def ok(j: dict) -> None:
            self._polling = False
            self.job = {**self.job, **j}
            self._render(j)

        run_async(lambda: self.api.job(jid), ok, lambda e: setattr(self, "_polling", False))

    def _mark(self, current: str) -> None:
        keys = [k for k, _ in (self.XEROX_STEPS if self.job["kind"] == "XEROX" else self.DIGITAL_STEPS)]
        idx = keys.index(current) if current in keys else -1
        for i, (k, lb) in enumerate(self._step_labels.items()):
            base = [t for kk, t in (self.XEROX_STEPS if self.job["kind"] == "XEROX" else self.DIGITAL_STEPS) if kk == k][0]
            if i < idx or (i == idx and current == "COMPLETED"):
                lb.setText(f"✓  {base}"); lb.setStyleSheet(f"color:{theme.GREEN}; font-size:16px; font-weight:700;")
            elif i == idx:
                lb.setText(f"●  {base}"); lb.setStyleSheet(f"color:{theme.BLUE}; font-size:16px; font-weight:800;")
            else:
                lb.setText(f"○  {base}"); lb.setStyleSheet(f"color:{theme.MUTED}; font-size:16px;")

    def _render(self, job: dict) -> None:
        st = job["state"]
        xerox = job["kind"] == "XEROX"
        if st in ("PAID", "QUEUED", "PRINTING", "AWAITING_STAFF"):
            self._mark("AWAITING_STAFF" if st == "AWAITING_STAFF" else st if st != "PAID" else "PAID")
            self.icon.setText("🖨" if not xerox else "▤")
            self.title.setText({"QUEUED": "You're in the queue", "PRINTING": "Printing now…",
                                "AWAITING_STAFF": "Hand your document to staff", "PAID": "Payment received"}[st])
            self.sub.setText("Give this code to the staff and hand over your original pages." if xerox
                             else "Please wait — you can stay here.")
        elif st == "COMPLETED":
            self.timer.stop(); self._mark("COMPLETED")
            self.icon.setText("✅")
            self.title.setText("All done!")
            self.sub.setText("Please collect your pages. Thank you!")
            self._arm_reset()
        elif st in ("FAILED", "UNCERTAIN"):
            self.timer.stop(); self._mark("")
            self.icon.setText("⚠")
            self.title.setText("Please see the staff")
            self.sub.setText("We had a problem printing your job. Your payment is safe — staff will reprint or refund it.\nShow them this code:")
            self._arm_reset(30)
        elif st in ("REFUND_PENDING", "REFUNDED"):
            self.timer.stop(); self.icon.setText("↩")
            self.title.setText("Refund in progress")
            self.sub.setText("Please see the staff with this code.")
            self._arm_reset(30)

    def _arm_reset(self, secs: int = 20) -> None:
        self._left = secs
        self.done.setVisible(True)
        self.done.setText(f"Start a new job ({self._left})")
        self.reset_timer.start()

    def _tick_reset(self) -> None:
        self._left -= 1
        self.done.setText(f"Start a new job ({self._left})")
        if self._left <= 0:
            self.reset_timer.stop(); self.finished.emit()

    def stop(self) -> None:
        self.timer.stop(); self.reset_timer.stop()
