"""Staff console pages: dashboard, queue, audit, admin."""
from __future__ import annotations

import copy
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QComboBox)

from ...shared.enums import STATE_LABELS, JobState, money
from .. import theme
from ..api import StaffApi
from ..asyncutil import run_async
from ..widgets.common import BarChart, Pill, card, label


def msg_err(parent: QWidget, e: Exception) -> None:
    QMessageBox.warning(parent, "SmartPrint", getattr(e, "detail", None) or str(e))


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setShowGrid(False)
    t.setAlternatingRowColors(False)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setDefaultSectionSize(40)
    return t


def cell(text: str, color: Optional[str] = None, bold: bool = False, align=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if color:
        it.setForeground(QColor(color))
    if bold:
        f = it.font(); f.setBold(True); it.setFont(f)
    if align:
        it.setTextAlignment(align)
    return it


def fmt_ts(ts: str) -> str:
    return ts.replace("T", " ")[:19] if ts else ""


# ------------------------------------------------------------------ dashboard
class StatCard(QFrame):
    def __init__(self, title: str, color: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self); lay.setContentsMargins(20, 16, 20, 16); lay.setSpacing(4)
        lay.addWidget(label(title.upper(), "eyebrow"))
        self.value = QLabel("—"); self.value.setStyleSheet(f"font-size:30px; font-weight:800; color:{color};")
        self.sub = label("", "muted")
        lay.addWidget(self.value); lay.addWidget(self.sub)

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value); self.sub.setText(sub)


class DashboardPage(QWidget):
    def __init__(self, api: StaffApi):
        super().__init__()
        self.api = api
        root = QVBoxLayout(self); root.setContentsMargins(28, 20, 28, 20); root.setSpacing(16)
        root.addWidget(label("Today in the shop", "h1"))
        row = QHBoxLayout(); row.setSpacing(16)
        self.rev = StatCard("Revenue", theme.GREEN); self.done = StatCard("Jobs completed", theme.BLUE)
        self.sides = StatCard("Sides printed", theme.NAVY); self.att = StatCard("Needs attention", theme.RED)
        for c in (self.rev, self.done, self.sides, self.att):
            row.addWidget(c)
        root.addLayout(row)
        mid = QHBoxLayout(); mid.setSpacing(16)
        c1 = card(); c1.layout().addWidget(label("Revenue by hour", "h2"))
        self.chart = BarChart(); c1.layout().addWidget(self.chart, 1)
        mid.addWidget(c1, 3)
        c2 = card(); c2.layout().addWidget(label("System", "h2"))
        self.sys = QLabel(""); self.sys.setTextFormat(Qt.RichText); self.sys.setWordWrap(True)
        c2.layout().addWidget(self.sys); c2.layout().addStretch(1)
        mid.addWidget(c2, 2)
        root.addLayout(mid, 1)

    def refresh(self) -> None:
        def stats(s: dict) -> None:
            cur = s["currency"]
            self.rev.set(money(s["revenue_minor"], cur), f"refunded {money(s['refunded_minor'], cur)}")
            self.done.set(str(s["jobs_completed"]), f"{s['digital_done']} digital · {s['xerox_done']} xerox")
            self.sides.set(str(s["sides_printed"]))
            self.att.set(str(s["attention"]), "failed / uncertain / refunds")
            self.chart.set_data([v / 100 for v in s["hourly_revenue"]], [f"{h:02d}" for h in range(24)])
        run_async(self.api.stats, stats, None)

        def system(s: dict) -> None:
            dot = lambda ok: f"<span style='color:{theme.GREEN if ok else theme.RED}'>●</span>"
            pcs = "".join(f"<br>{dot(w['online'])} {w['name']}" + ("" if w["active"] else " <i>(disabled)</i>") for w in s["workstations"])
            self.sys.setText(f"{dot(s['printer']['ok'])} Printer ({s['printer']['backend']})<br>"
                             f"{dot(s['worker_alive'])} Print worker<br>"
                             f"{dot(True)} Xerox: {'automated' if s['xerox_automation'] else 'staff-assisted'}<br><br>"
                             f"<b>Shop PCs</b>{pcs}")
        run_async(self.api.system, system, None)


# ------------------------------------------------------------------ queue
FILTERS = [("Active jobs", {"active_only": True}), ("Needs attention", {"state": "FAILED,UNCERTAIN,REFUND_PENDING"}),
           ("Waiting for staff (xerox)", {"state": "AWAITING_STAFF"}), ("Awaiting payment", {"state": "AWAITING_PAYMENT"}),
           ("Everything", {})]


class QueuePage(QWidget):
    def __init__(self, api: StaffApi):
        super().__init__()
        self.api = api
        self.jobs: list[dict] = []
        self.selected: Optional[str] = None
        root = QHBoxLayout(self); root.setContentsMargins(28, 20, 28, 20); root.setSpacing(18)
        left = QVBoxLayout()
        bar = QHBoxLayout()
        self.filter = QComboBox()
        for name, _ in FILTERS:
            self.filter.addItem(name)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search code or file name…  e.g. SP-7K3Q")
        bar.addWidget(self.filter); bar.addWidget(self.search, 1)
        left.addLayout(bar)
        self.table = table(["Code", "Type", "Status", "Job", "Total", "PC", "Updated"])
        hdr = self.table.horizontalHeader()
        for col, width in ((0, 92), (1, 64), (2, 190), (4, 96), (5, 70)):
            self.table.setColumnWidth(col, width)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)          # file name takes the slack
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        left.addWidget(self.table, 1)
        root.addLayout(left, 5)

        self.detail = card(margins=22, spacing=10)
        self.d_code = label("Select a job", "h1")
        self.d_pill = Pill("", theme.MUTED)
        self.d_info = QLabel(""); self.d_info.setTextFormat(Qt.RichText); self.d_info.setWordWrap(True)
        self.d_err = label("", wrap=True); self.d_err.setStyleSheet(f"color:{theme.RED}; font-weight:600;")
        self.detail.layout().addWidget(self.d_code)
        self.detail.layout().addWidget(self.d_pill)
        self.detail.layout().addWidget(self.d_info)
        self.detail.layout().addWidget(self.d_err)
        self.detail.layout().addStretch(1)
        self.actions = QVBoxLayout(); self.actions.setSpacing(8)
        self.detail.layout().addLayout(self.actions)
        self.detail.setMinimumWidth(340)
        root.addWidget(self.detail, 3)

        self.filter.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(lambda: QTimer.singleShot(300, self.refresh))
        self.table.itemSelectionChanged.connect(self._select)

    def refresh(self) -> None:
        params = dict(FILTERS[self.filter.currentIndex()][1])
        params["q"] = self.search.text().strip()
        run_async(lambda: self.api.jobs(**params), self._fill, None)

    def _fill(self, jobs: list[dict]) -> None:
        self.jobs = jobs
        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(jobs))
        sel_row = -1
        for r, j in enumerate(jobs):
            col = theme.STATE_COLOR.get(j["state"], theme.MUTED)
            t.setItem(r, 0, cell(j["code"], bold=True))
            t.setItem(r, 1, cell("COPY" if j["kind"] == "XEROX" else "PRINT", theme.MUTED))
            t.setItem(r, 2, cell("● " + j["state_label"], col, bold=True))
            t.setItem(r, 3, cell(j["original_name"] or "Xerox copy"))
            t.setItem(r, 4, cell(money(j["total_minor"], j["currency"]) if j["total_minor"] else "—", align=Qt.AlignRight | Qt.AlignVCenter))
            t.setItem(r, 5, cell(j["workstation"] or ""))
            t.setItem(r, 6, cell(fmt_ts(j["updated_at"])[11:]))
            if j["id"] == self.selected:
                sel_row = r
        if sel_row >= 0:
            t.selectRow(sel_row)
        t.blockSignals(False)
        self._render_detail()

    def _select(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        self.selected = self.jobs[rows[0].row()]["id"] if rows else None
        self._render_detail()

    def current(self) -> Optional[dict]:
        return next((j for j in self.jobs if j["id"] == self.selected), None)

    def _render_detail(self) -> None:
        while self.actions.count():
            it = self.actions.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        j = self.current()
        if not j:
            self.d_code.setText("Select a job"); self.d_pill.set("", theme.MUTED); self.d_pill.setVisible(False)
            self.d_info.clear(); self.d_err.clear()
            return
        self.d_pill.setVisible(True)
        self.d_code.setText(j["code"])
        self.d_pill.set(j["state_label"], theme.STATE_COLOR.get(j["state"], theme.MUTED))
        s, q, p = j["settings"] or {}, j["quote"] or {}, j["payment"]
        rows = [("Type", "Xerox copy (staff-assisted)" if j["kind"] == "XEROX" else f"Digital print — {j['original_name']}"),
                ("Pages", f"{q.get('pages_selected', '—')} × {s.get('copies', '—')} copies = {q.get('sides', '—')} sides"),
                ("Options", f"{'Colour' if s.get('color') == 'COLOR' else 'B/W'} · {s.get('paper', '')} · {'double' if s.get('duplex') else 'single'}-sided" if s else "—"),
                ("Total", money(j["total_minor"], j["currency"])),
                ("Payment", f"{p['method']} · {p['state']} · {p['reference']}" if p else "—"),
                ("PC", j["workstation"] or "—"), ("Attempts", str(j["attempts"]))]
        if j["staff_note"]:
            rows.append(("Note", j["staff_note"]))
        self.d_info.setText("<table cellspacing=6>" + "".join(
            f"<tr><td style='color:{theme.MUTED}'>{k}&nbsp;&nbsp;</td><td><b>{v}</b></td></tr>" for k, v in rows) + "</table>")
        self.d_err.setText(f"⚠ {j['last_error']}" if j["last_error"] else "")
        st = JobState(j["state"])
        counter_pending = bool(p and p["method"] == "COUNTER" and p["state"] == "PENDING")
        if st == JobState.AWAITING_PAYMENT and counter_pending:
            self._btn("💵  Confirm cash received…", self._confirm_cash, "success")
        if st in (JobState.FAILED, JobState.UNCERTAIN):
            self._btn("↻  Re-queue (re-authorise print)", lambda: self._act("retry", "Why is it safe to print again?"), "primary")
        if st == JobState.UNCERTAIN:
            self._btn("✓  Pages are in the tray — mark printed", lambda: self._act("complete", "Note (optional)", required=False))
        if st == JobState.AWAITING_STAFF:
            self._btn("✓  Copy done — hand over to customer", lambda: self._act("complete", "Note (optional)", required=False), "success")
        if st in (JobState.FAILED, JobState.UNCERTAIN, JobState.AWAITING_STAFF, JobState.QUEUED, JobState.PAID,
                  JobState.AWAITING_PAYMENT) and (p and p["state"] in ("PAID", "MISMATCH")):
            self._btn("↩  Refund this job…", lambda: self._act("refund-request", "Reason for refund"), "danger")
        if st == JobState.REFUND_PENDING:
            self._btn("💸  Refund handed back — complete", lambda: self._act("refund", "Note (optional)", required=False), "success")

    def _btn(self, text: str, cb, name: str = "") -> None:
        b = QPushButton(text)
        if name:
            b.setObjectName(name)
        b.clicked.connect(lambda: cb())
        self.actions.addWidget(b)

    def _confirm_cash(self) -> None:
        j = self.current()
        v, ok = QInputDialog.getInt(self, "Confirm cash", f"Quote is {money(j['total_minor'], j['currency'])}.\nEnter the amount you received (whole {j['currency']}):", 0, 0, 10_000_000)
        if ok:
            self._run(j, "confirm-cash", "", v * 100)

    def _act(self, action: str, prompt: str, required: bool = True) -> None:
        j = self.current()
        note, ok = QInputDialog.getText(self, "SmartPrint", prompt)
        if ok and (note.strip() or not required):
            self._run(j, action, note.strip())

    def _run(self, j: dict, action: str, note: str, amount: Optional[int] = None) -> None:
        run_async(lambda: self.api.action(j["id"], action, note, amount), lambda _: self.refresh(), lambda e: (msg_err(self, e), self.refresh()))


# ------------------------------------------------------------------ audit
class AuditPage(QWidget):
    def __init__(self, api: StaffApi):
        super().__init__()
        self.api = api
        root = QVBoxLayout(self); root.setContentsMargins(28, 20, 28, 20); root.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("Audit trail", "h1")); top.addStretch(1)
        self.verify = QPushButton("🔒  Verify integrity"); self.verify.setObjectName("primary"); self.verify.clicked.connect(self._verify)
        top.addWidget(self.verify)
        root.addLayout(top)
        self.banner = label("Every event is hash-chained: if anyone edits or deletes a row, verification fails.", "muted", wrap=True)
        root.addWidget(self.banner)
        self.table = table(["#", "Time", "Actor", "Action", "Entity", "Details"])
        for i, w in enumerate((60, 150, 140, 200, 110)):
            self.table.setColumnWidth(i, w)
        root.addWidget(self.table, 1)

    def refresh(self) -> None:
        run_async(lambda: self.api.audit(300), self._fill, None)

    def _fill(self, evs: list[dict]) -> None:
        t = self.table
        t.setRowCount(len(evs))
        for r, e in enumerate(evs):
            d = e["data"]
            code = d.get("code", "")
            detail = d.get("reason") or ", ".join(f"{k}={v}" for k, v in d.items() if k not in ("code", "from", "to", "reason"))
            warn = e["action"].startswith("security.") or e["action"].endswith(("mismatch", "failed", "rejected"))
            t.setItem(r, 0, cell(str(e["seq"]), theme.MUTED))
            t.setItem(r, 1, cell(fmt_ts(e["ts"])[5:]))
            t.setItem(r, 2, cell(e["actor"]))
            t.setItem(r, 3, cell(e["action"], theme.RED if warn else theme.INK, bold=True))
            t.setItem(r, 4, cell(code or e["entity_id"][:8]))
            t.setItem(r, 5, cell(str(detail)[:140]))

    def _verify(self) -> None:
        def ok(r: dict) -> None:
            if r["ok"]:
                self.banner.setText(f"✔ Audit chain intact — {r['checked']} events verified.")
                self.banner.setStyleSheet(f"color:{theme.GREEN}; font-weight:700;")
            else:
                self.banner.setText(f"✖ TAMPERING DETECTED at event #{r['broken_at']}: {r['reason']}")
                self.banner.setStyleSheet(f"color:{theme.RED}; font-weight:800;")
        run_async(self.api.verify_audit, ok, lambda e: msg_err(self, e))


# ------------------------------------------------------------------ admin
class PricingEditor(QWidget):
    def __init__(self, api: StaffApi):
        super().__init__()
        self.api = api
        self.cfg: dict[str, Any] = {}
        root = QVBoxLayout(self); root.setSpacing(12)
        self.version = label("", "muted")
        root.addWidget(self.version)
        grid = QGridLayout(); grid.setHorizontalSpacing(12)
        self.cells: dict[tuple[str, str, str], QDoubleSpinBox] = {}
        for c, h in enumerate(["", "A4 B/W", "A4 Colour", "A3 B/W", "A3 Colour", "Letter B/W", "Letter Colour"]):
            grid.addWidget(label(h, "muted"), 0, c)
        for r, svc in enumerate(("digital", "xerox"), start=1):
            grid.addWidget(label("Digital print" if svc == "digital" else "Xerox copy", "h2"), r, 0)
            c = 1
            for paper in ("A4", "A3", "LETTER"):
                for color in ("BW", "COLOR"):
                    sp = QDoubleSpinBox(); sp.setRange(0.5, 100000); sp.setDecimals(2); sp.setPrefix("Rs ")
                    self.cells[(svc, paper, color)] = sp
                    grid.addWidget(sp, r, c); c += 1
        root.addLayout(grid)
        form = QFormLayout()
        self.duplex = QSpinBox(); self.duplex.setRange(0, 90); self.duplex.setSuffix(" % off per side")
        self.minimum = QDoubleSpinBox(); self.minimum.setRange(0, 100000); self.minimum.setPrefix("Rs ")
        form.addRow("Double-sided discount", self.duplex); form.addRow("Minimum charge", self.minimum)
        self.tiers: list[tuple[QSpinBox, QSpinBox]] = []
        for i in range(3):
            a, b = QSpinBox(), QSpinBox(); a.setRange(1, 1_000_000); a.setSuffix(" sides+"); b.setRange(0, 90); b.setSuffix(" % off")
            h = QHBoxLayout(); h.addWidget(a); h.addWidget(b); h.addStretch(1)
            form.addRow(f"Volume tier {i + 1}", h); self.tiers.append((a, b))
        root.addLayout(form)
        note = label("Publishing creates a new immutable version. Jobs already quoted keep the version that priced them.", "muted", wrap=True)
        root.addWidget(note)
        self.pub = QPushButton("Publish new price list"); self.pub.setObjectName("primary"); self.pub.clicked.connect(self.publish)
        root.addWidget(self.pub, 0, Qt.AlignLeft); root.addStretch(1)

    def load(self) -> None:
        def ok(rows: list[dict]) -> None:
            cur = next(r for r in rows if r["active"])
            self.cfg = copy.deepcopy(cur["config"])
            self.version.setText(f"Active version v{cur['version']} · {len(rows)} version(s) in history")
            for (svc, paper, color), sp in self.cells.items():
                sp.setValue(self.cfg["services"][svc][paper][color] / 100)
            self.duplex.setValue(self.cfg.get("duplex_discount_pct", 0))
            self.minimum.setValue(self.cfg.get("minimum_charge_minor", 0) / 100)
            tiers = self.cfg.get("volume_tiers", [])
            for i, (a, b) in enumerate(self.tiers):
                a.setValue(tiers[i]["min_sides"] if i < len(tiers) else 1); b.setValue(tiers[i]["discount_pct"] if i < len(tiers) else 0)
        run_async(self.api.pricing, ok, lambda e: msg_err(self, e))

    def publish(self) -> None:
        cfg = copy.deepcopy(self.cfg)
        for (svc, paper, color), sp in self.cells.items():
            cfg["services"][svc][paper][color] = int(round(sp.value() * 100))
        cfg["duplex_discount_pct"] = self.duplex.value()
        cfg["minimum_charge_minor"] = int(round(self.minimum.value() * 100))
        cfg["volume_tiers"] = [{"min_sides": a.value(), "discount_pct": b.value()} for a, b in self.tiers if b.value() > 0]
        run_async(lambda: self.api.publish_pricing(cfg), lambda r: (QMessageBox.information(self, "SmartPrint", f"Published price list v{r['version']}"), self.load()),
                  lambda e: msg_err(self, e))


class AdminPage(QWidget):
    def __init__(self, api: StaffApi):
        super().__init__()
        self.api = api
        root = QVBoxLayout(self); root.setContentsMargins(28, 20, 28, 20)
        self.tabs = QTabWidget(); root.addWidget(self.tabs)
        self.pricing = PricingEditor(api)
        w = QWidget(); wl = QVBoxLayout(w); wl.setContentsMargins(0, 16, 0, 0); wl.addWidget(self.pricing)
        self.tabs.addTab(w, "Pricing")

        pcs = QWidget(); pl = QVBoxLayout(pcs); pl.setContentsMargins(0, 16, 0, 0)
        self.pc_table = table(["Name", "Printer", "Active", "Last seen"]); pl.addWidget(self.pc_table, 1)
        row = QHBoxLayout()
        self.pc_name = QLineEdit(); self.pc_name.setPlaceholderText("New PC name, e.g. PC-05")
        add = QPushButton("Register PC"); add.setObjectName("primary"); add.clicked.connect(self._add_pc)
        tog = QPushButton("Enable / disable selected"); tog.clicked.connect(self._toggle_pc)
        row.addWidget(self.pc_name, 1); row.addWidget(add); row.addWidget(tog)
        pl.addLayout(row)
        self.tabs.addTab(pcs, "Shop PCs")

        st = QWidget(); sl = QVBoxLayout(st); sl.setContentsMargins(0, 16, 0, 0)
        self.staff_table = table(["Username", "Role", "Active"]); sl.addWidget(self.staff_table, 1)
        r2 = QHBoxLayout()
        self.su, self.sp = QLineEdit(), QLineEdit(); self.su.setPlaceholderText("username"); self.sp.setPlaceholderText("password (8+ chars)")
        self.sp.setEchoMode(QLineEdit.Password)
        self.sr = QComboBox(); self.sr.addItems(["OPERATOR", "ADMIN"])
        addu = QPushButton("Add staff"); addu.setObjectName("primary"); addu.clicked.connect(self._add_staff)
        for x in (self.su, self.sp, self.sr, addu):
            r2.addWidget(x)
        sl.addLayout(r2)
        self.tabs.addTab(st, "Staff")
        self._pcs: list[dict] = []

    def refresh(self) -> None:
        self.pricing.load()
        run_async(self.api.workstations, self._fill_pcs, None)
        run_async(self.api.staff_list, self._fill_staff, None)

    def _fill_pcs(self, rows: list[dict]) -> None:
        self._pcs = rows
        self.pc_table.setRowCount(len(rows))
        for r, w in enumerate(rows):
            self.pc_table.setItem(r, 0, cell(w["name"], bold=True)); self.pc_table.setItem(r, 1, cell(w["printer_name"] or "default"))
            self.pc_table.setItem(r, 2, cell("yes" if w["active"] else "disabled", theme.GREEN if w["active"] else theme.RED))
            self.pc_table.setItem(r, 3, cell(fmt_ts(w["last_seen_at"] or "") or "never"))

    def _fill_staff(self, rows: list[dict]) -> None:
        self.staff_table.setRowCount(len(rows))
        for r, u in enumerate(rows):
            self.staff_table.setItem(r, 0, cell(u["username"], bold=True)); self.staff_table.setItem(r, 1, cell(u["role"]))
            self.staff_table.setItem(r, 2, cell("yes" if u["active"] else "no"))

    def _add_pc(self) -> None:
        name = self.pc_name.text().strip()
        if not name:
            return

        def ok(r: dict) -> None:
            self.pc_name.clear()
            QInputDialog.getText(self, "API key (shown once)", f"Put this in {r['name']}'s client.json as pc_key:", QLineEdit.Normal, r["api_key"])
            self.refresh()
        run_async(lambda: self.api.register_ws(name), ok, lambda e: msg_err(self, e))

    def _toggle_pc(self) -> None:
        rows = self.pc_table.selectionModel().selectedRows()
        if rows:
            w = self._pcs[rows[0].row()]
            run_async(lambda: self.api.set_ws_active(w["id"], not w["active"]), lambda _: self.refresh(), lambda e: msg_err(self, e))

    def _add_staff(self) -> None:
        u, p, r = self.su.text().strip(), self.sp.text(), self.sr.currentText()
        run_async(lambda: self.api.create_staff(u, p, r), lambda _: (self.su.clear(), self.sp.clear(), self.refresh()), lambda e: msg_err(self, e))
