"""The two PySide6 apps, driven headlessly (offscreen) against a real embedded server."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smartprint import demo  # noqa: E402
from smartprint.client import theme  # noqa: E402
from smartprint.client.api import StaffApi  # noqa: E402
from smartprint.client.asyncutil import run_async  # noqa: E402
from smartprint.client.kiosk_app import PAY, PRINT, SETTINGS, KioskWindow  # noqa: E402
from smartprint.client.settings import ClientSettings  # noqa: E402
from smartprint.client.staff_app import StaffWindow  # noqa: E402
from smartprint.client.widgets.common import NumberStepper, Segmented  # noqa: E402
from smartprint.server.cli import create_admin, register_pc  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.QSS)
    return app


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    s = demo.Shop(tmp_path_factory.mktemp("gui"), demo.free_port())
    s.ctx.db.create_all()
    s.start()
    create_admin(s.ctx, "admin", "admin12345", "ADMIN")
    s.key = register_pc(s.ctx, "PC-01")
    yield s
    s.stop()


def spin(cond, timeout=15.0):
    """Run the Qt event loop until cond() is true (queued cross-thread signals need a real loop)."""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        loop = QEventLoop()
        QTimer.singleShot(40, loop.quit)
        loop.exec()
    return cond()


def test_run_async_delivers_results_and_errors_on_the_gui_thread(qapp):
    got = []
    run_async(lambda: 6 * 7, lambda r: got.append(("ok", r)), lambda e: got.append(("err", e)))
    run_async(lambda: 1 / 0, lambda r: got.append(("ok", r)), lambda e: got.append(("err", type(e).__name__)))
    assert spin(lambda: len(got) == 2)
    assert ("ok", 42) in got and ("err", "ZeroDivisionError") in got


def test_widgets(qapp):
    s = NumberStepper(1, 5, 2)
    seen = []
    s.valueChanged.connect(seen.append)
    s.plus.click(); s.plus.click(); s.minus.click()
    assert s.value() == 3 and seen == [3, 4, 3]
    seg = Segmented([("Black & White", "BW"), ("Colour", "COLOR")])
    assert seg.group.button(0).text() == "Black && White"      # '&&' renders as a literal '&' (no mnemonic)


def test_customer_journey_file_settings_pay_print(qapp, shop, tmp_path):
    pdf = tmp_path / "thesis.pdf"
    pdf.write_bytes(demo.sample_pdf(8))
    w = KioskWindow(ClientSettings(server=shop.base, pc_name="PC-01", pc_key=shop.key))
    w.show()
    assert spin(lambda: "online" in w.conn.text()), w.conn.text()

    w.start_digital(pdf)                                         # FILE
    assert spin(lambda: w.stack.currentIndex() == SETTINGS and w.settings.job.get("id"))
    assert w.settings.job["page_count"] == 8

    w.settings.copies.setValue(2)                                # SETTINGS (live server-side price)
    assert spin(lambda: w.settings.go.isEnabled() and "PKR 160" in w.settings.total.text()), w.settings.total.text()

    w.settings.go.click()                                        # PAY
    assert spin(lambda: w.stack.currentIndex() == PAY and w.pay.job.get("payment"))
    assert w.pay.job["payment"]["amount_minor"] == 16000
    w.pay._simulate()                                            # provider webhook (signed) in dev mode

    assert spin(lambda: w.stack.currentIndex() == PRINT)         # PRINT
    assert spin(lambda: w.printp.job.get("state") == "COMPLETED", timeout=20), w.printp.job.get("state")
    w.close()


def test_staff_console_sees_the_job_and_audit_verifies(qapp, shop):
    api = StaffApi(ClientSettings(server=shop.base, pc_name="PC-01"))
    api.login("admin", "admin12345")
    w = StaffWindow(api)
    w.show()
    w.tabs.setCurrentIndex(1)
    w.queue.filter.setCurrentIndex(w.queue.filter.count() - 1)   # "Everything"
    assert spin(lambda: w.queue.table.rowCount() >= 1)
    assert api.verify_audit()["ok"] is True
    w.close()
