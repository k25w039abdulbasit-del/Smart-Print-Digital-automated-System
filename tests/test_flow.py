"""End-to-end: FILE -> SETTINGS -> PAY -> PRINT through the real HTTP API."""
import json
from pathlib import Path

from conftest import make_pdf
from pypdf import PdfReader


def test_full_digital_flow_prints_exactly_what_was_paid(env):
    job = env.upload(pages=5, name="../../etc/report.pdf")
    assert job["state"] == "DRAFT" and job["page_count"] == 5
    assert job["original_name"] == "report.pdf"            # path stripped

    j = env.configure(job, copies=2, page_range="1-3", duplex=True)
    assert j["state"] == "CONFIGURED"
    assert j["total_minor"] == 5400
    assert j["quote"]["sides"] == 6

    j = env.pay_online(job, simulate=False)
    assert j["state"] == "AWAITING_PAYMENT" and j["payment"]["state"] == "PENDING"
    assert env.worker.run_once() is False                   # nothing printable before payment

    env.client.post(f"/api/v1/dev/payments/{j['payment']['reference']}/simulate", headers=env.pc)
    assert env.get(job)["state"] == "QUEUED"

    assert env.worker.run_once() is True
    done = env.get(job)
    assert done["state"] == "COMPLETED"

    out = next(Path(env.settings.spool_dir).glob(f"{job['code']}-a1/output.pdf"))
    assert len(PdfReader(str(out)).pages) == 6              # 3 pages x 2 copies
    ticket = json.loads((out.parent / "ticket.json").read_text())
    assert ticket["duplex"] is True and ticket["pages"] == [1, 2, 3]


def test_client_cannot_influence_price(env):
    job = env.upload(pages=2)
    r = env.client.put(f"/api/v1/jobs/{job['id']}/settings", headers=env.h(job),
                       json={"copies": 1, "total_minor": 1, "price": 1})
    assert r.status_code == 200 and r.json()["total_minor"] == 2000     # unknown fields ignored


def test_settings_frozen_after_payment_requested(env):
    job = env.upload()
    env.configure(job)
    env.pay_online(job, simulate=False)
    r = env.client.put(f"/api/v1/jobs/{job['id']}/settings", headers=env.h(job), json={"copies": 9})
    assert r.status_code == 409


def test_job_token_required_and_isolated(env):
    a, b = env.upload(), env.upload()
    r = env.client.get(f"/api/v1/jobs/{a['id']}", headers={**env.pc, "X-Job-Token": b["access_token"]})
    assert r.status_code == 404
    assert env.client.get(f"/api/v1/jobs/{a['id']}", headers=env.pc).status_code == 404
    assert env.client.get(f"/api/v1/jobs/{a['id']}").status_code == 401


def test_unknown_pc_key_rejected(env):
    r = env.client.post("/api/v1/jobs/xerox", headers={"X-PC-Key": "nope"})
    assert r.status_code == 401


def test_pay_is_idempotent(env):
    job = env.upload()
    env.configure(job)
    h = {**env.h(job), "Idempotency-Key": "abc"}
    r1 = env.client.post(f"/api/v1/jobs/{job['id']}/pay", headers=h, json={"method": "ONLINE"}).json()
    r2 = env.client.post(f"/api/v1/jobs/{job['id']}/pay", headers=h, json={"method": "ONLINE"}).json()
    assert r1["payment"]["reference"] == r2["payment"]["reference"]


def test_switching_payment_method_cancels_old_request(env):
    job = env.upload()
    env.configure(job)
    online = env.pay_online(job, simulate=False)
    r = env.client.post(f"/api/v1/jobs/{job['id']}/pay", headers=env.h(job), json={"method": "COUNTER"})
    assert r.json()["payment"]["method"] == "COUNTER"
    assert r.json()["payment"]["reference"] != online["payment"]["reference"]


def test_cancel_unpaid_job(env):
    job = env.upload()
    env.configure(job)
    env.pay_online(job, simulate=False)
    r = env.client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=env.h(job))
    assert r.json()["state"] == "CANCELLED" and r.json()["payment"]["state"] == "CANCELLED"


def test_cannot_cancel_paid_job_as_customer(env):
    job = env.paid_job()
    assert env.client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=env.h(job)).status_code == 409


def test_counter_payment_requires_exact_cash(env):
    job = env.upload(pages=3)
    env.configure(job)
    env.client.post(f"/api/v1/jobs/{job['id']}/pay", headers=env.h(job), json={"method": "COUNTER"})
    st = env.staff("op", "operator12345")
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/confirm-cash", headers=st, json={"amount_minor": 1000})
    assert r.status_code == 409
    assert env.get(job)["state"] == "AWAITING_PAYMENT" and env.get(job)["payment"]["state"] == "MISMATCH"
    # held payment blocks a second payment attempt until staff resolves it
    assert env.client.post(f"/api/v1/jobs/{job['id']}/pay", headers=env.h(job), json={"method": "ONLINE"}).status_code == 409
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/refund-request", headers=st, json={"note": "short cash"})
    assert r.json()["state"] == "REFUND_PENDING"
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/refund", headers=st, json={})
    assert r.json()["state"] == "REFUNDED"


def test_counter_payment_happy_path(env):
    job = env.upload(pages=3)
    env.configure(job)
    env.client.post(f"/api/v1/jobs/{job['id']}/pay", headers=env.h(job), json={"method": "COUNTER"})
    st = env.staff("op", "operator12345")
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/confirm-cash", headers=st, json={"amount_minor": 3000})
    assert r.json()["state"] == "QUEUED"
    env.run_worker_until_idle()
    assert env.get(job)["state"] == "COMPLETED"


def test_xerox_flow_is_staff_assisted(env):
    r = env.client.post("/api/v1/jobs/xerox", headers=env.pc)
    job = r.json()
    j = env.configure(job, originals=10, copies=2, color="COLOR")
    assert j["total_minor"] == 10 * 2 * 2500 * 1  # 50000 -> 5% not reached (20 sides)
    env.pay_online(job)
    assert env.get(job)["state"] == "AWAITING_STAFF"
    assert env.worker.run_once() is False                       # never goes to the printer
    st = env.staff("op", "operator12345")
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/complete", headers=st, json={"note": "copied"})
    assert r.json()["state"] == "COMPLETED"


def test_preview_png(env):
    job = env.upload(pages=2)
    r = env.client.get(f"/api/v1/jobs/{job['id']}/preview?page=2", headers=env.h(job))
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert env.client.get(f"/api/v1/jobs/{job['id']}/preview?page=3", headers=env.h(job)).status_code == 404


def test_rejects_bad_files(env):
    def up(name, data):
        return env.client.post("/api/v1/jobs/digital", headers=env.pc, files={"file": (name, data)})
    assert up("x.pdf", b"").status_code == 422
    assert up("x.pdf", b"not a pdf at all").status_code == 422
    assert up("evil.exe", b"MZ\x90\x00").status_code == 422
    assert up("fake.pdf", b"%PDF-1.4\ngarbage").status_code == 422


def test_image_and_text_are_normalised_to_pdf(env):
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "navy").save(buf, "PNG")
    r = env.client.post("/api/v1/jobs/digital", headers=env.pc, files={"file": ("pic.png", buf.getvalue())})
    assert r.status_code == 201 and r.json()["page_count"] == 1
    t = env.client.post("/api/v1/jobs/digital", headers=env.pc, files={"file": ("n.txt", ("line\n" * 200).encode())})
    assert t.status_code == 201 and t.json()["page_count"] >= 3


def test_upload_size_limit(env):
    env.settings.max_upload_mb = 1
    r = env.client.post("/api/v1/jobs/digital", headers=env.pc, files={"file": ("big.pdf", b"%PDF-" + b"0" * (2 * 1024 * 1024))})
    assert r.status_code in (413, 422)


def test_health(env):
    h = env.client.get("/health").json()
    assert h["database"] is True and h["status"] == "ok"


def test_customer_can_withdraw_payment_to_change_options(env):
    job = env.upload(pages=2)
    env.configure(job)
    env.pay_online(job, simulate=False)
    r = env.client.post(f"/api/v1/jobs/{job['id']}/payment/withdraw", headers=env.h(job))
    assert r.json()["state"] == "CONFIGURED" and r.json()["payment"]["state"] == "CANCELLED"
    assert env.configure(job, copies=3)["total_minor"] == 6000          # editable again, repriced
    # paid job cannot be withdrawn
    paid = env.paid_job()
    assert env.client.post(f"/api/v1/jobs/{paid['id']}/payment/withdraw", headers=env.h(paid)).status_code == 409
