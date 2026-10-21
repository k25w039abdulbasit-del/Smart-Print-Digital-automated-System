import json
import time

from smartprint.server.adapters.payment import SIG_HEADER, TS_HEADER, sign_webhook
from smartprint.server.models import Job


def hook(env, body: dict, secret=None, ts=None):
    raw = json.dumps(body).encode()
    return env.client.post("/api/v1/payments/webhook/mock", content=raw,
                           headers=sign_webhook(secret or env.settings.webhook_secret, raw, ts))


def evt(pay, **over):
    base = dict(event_id="evt_1", reference=pay["reference"], status="succeeded",
                amount_minor=pay["amount_minor"], currency=pay["currency"], txn_id="t1")
    return {**base, **over}


def awaiting(env, pages=3):
    job = env.upload(pages)
    env.configure(job)
    return job, env.pay_online(job, simulate=False)["payment"]


def test_valid_webhook_authorises_job(env):
    job, pay = awaiting(env)
    r = hook(env, evt(pay))
    assert r.json()["outcome"] == "authorised"
    assert env.get(job)["state"] == "QUEUED"


def test_forged_signature_rejected_and_nothing_changes(env):
    job, pay = awaiting(env)
    assert hook(env, evt(pay), secret="attacker").status_code == 400
    assert env.get(job)["state"] == "AWAITING_PAYMENT"


def test_unsigned_and_stale_webhooks_rejected(env):
    job, pay = awaiting(env)
    assert env.client.post("/api/v1/payments/webhook/mock", json=evt(pay)).status_code == 400
    assert hook(env, evt(pay), ts=int(time.time()) - 3600).status_code == 400
    assert env.get(job)["state"] == "AWAITING_PAYMENT"


def test_replayed_event_is_ignored(env):
    job, pay = awaiting(env)
    assert hook(env, evt(pay)).json()["outcome"] == "authorised"
    assert hook(env, evt(pay)).json()["outcome"] == "replay"


def test_duplicate_success_with_new_event_id_is_idempotent(env):
    job, pay = awaiting(env)
    hook(env, evt(pay))
    assert hook(env, evt(pay, event_id="evt_2")).json()["outcome"] == "duplicate"
    env.run_worker_until_idle()
    assert env.get(job)["state"] == "COMPLETED"       # printed once, not twice
    assert len(list(env.settings.spool_dir.glob(f"{job['code']}-*"))) == 1


def test_underpayment_is_held_not_authorised(env):
    job, pay = awaiting(env)
    assert hook(env, evt(pay, amount_minor=100)).json()["outcome"] == "mismatch"
    g = env.get(job)
    assert g["state"] == "AWAITING_PAYMENT" and g["payment"]["state"] == "MISMATCH"
    assert env.worker.run_once() is False


def test_wrong_currency_is_held(env):
    job, pay = awaiting(env)
    assert hook(env, evt(pay, currency="USD")).json()["outcome"] == "mismatch"


def test_unknown_reference_is_harmless(env):
    assert hook(env, dict(event_id="e9", reference="PAY-NOPE", status="succeeded", amount_minor=1,
                          currency="PKR", txn_id="x")).json()["outcome"] == "unknown"


def test_late_payment_on_cancelled_job_goes_to_refund(env):
    job, pay = awaiting(env)
    env.client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=env.h(job))
    assert hook(env, evt(pay)).json()["outcome"] == "late_refund_pending"
    assert env.get(job)["state"] == "REFUND_PENDING"


def test_failed_payment_lets_customer_retry(env):
    job, pay = awaiting(env)
    hook(env, evt(pay, status="failed"))
    assert env.get(job)["state"] == "CONFIGURED"
    assert env.pay_online(job, simulate=True)["state"] == "AWAITING_PAYMENT"   # new request works


def test_print_authorisation_detects_tampering(env):
    """Even with direct DB access an attacker/bug cannot print unpaid-for settings."""
    job = env.paid_job(pages=2, copies=1)
    with env.ctx.db.session() as db:
        j = db.get(Job, job["id"])
        j.settings = {**j.settings, "copies": 50}      # edit settings after paying for 1 copy
    env.worker.run_once()
    g = env.get(job)
    assert g["state"] == "FAILED" and "authorisation" in g["last_error"]
    assert not list(env.settings.spool_dir.glob("*"))


def test_print_refuses_swapped_file(env):
    job = env.paid_job(pages=2)
    with env.ctx.db.session() as db:
        j = db.get(Job, job["id"])
        path = env.settings.files_dir / j.stored_name
    path.write_bytes(path.read_bytes() + b"\n%tampered")
    env.worker.run_once()
    assert env.get(job)["state"] == "FAILED"
    assert "does not match" in env.get(job)["last_error"]


def test_authorisation_is_single_use(env):
    job = env.paid_job()
    with env.ctx.db.session() as db:
        j = db.get(Job, job["id"])
        j.auth_consumed_at = j.created_at      # pretend it was used already, job wrongly back in QUEUED
    env.worker.run_once()
    assert env.get(job)["state"] == "FAILED"


def test_staff_endpoints_need_login_and_admin_for_admin(env):
    assert env.client.get("/api/v1/staff/jobs").status_code == 401
    op = env.staff("op", "operator12345")
    assert env.client.get("/api/v1/staff/jobs", headers=op).status_code == 200
    assert env.client.get("/api/v1/admin/pricing", headers=op).status_code == 403
    assert env.client.get("/api/v1/admin/pricing", headers=env.staff()).status_code == 200


def test_login_throttle_and_bad_token(env):
    for _ in range(5):
        assert env.client.post("/api/v1/staff/login", json={"username": "op", "password": "bad"}).status_code == 401
    assert env.client.post("/api/v1/staff/login", json={"username": "op", "password": "operator12345"}).status_code == 429
    assert env.client.get("/api/v1/staff/me", headers={"Authorization": "Bearer abc.def"}).status_code == 401


def test_dev_simulator_disabled_in_prod(env):
    env.settings.environment = "prod"
    job, pay = awaiting(env)
    assert env.client.post(f"/api/v1/dev/payments/{pay['reference']}/simulate", headers=env.pc).status_code == 404
