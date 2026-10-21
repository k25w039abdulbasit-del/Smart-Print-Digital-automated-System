from datetime import timedelta

from smartprint.server.adapters.printer import PrintError
from smartprint.server.db import utcnow
from smartprint.server.models import AuditEvent, Job, Payment
from smartprint.server.services import audit


def states(env, job):
    return env.get(job)["state"]


def test_retryable_printer_error_retries_then_succeeds(env):
    job = env.paid_job()
    env.ctx.printer.fail_next = PrintError("paper jam", retryable=True)
    env.worker.run_once()
    g = env.get(job)
    assert g["state"] == "QUEUED" and g["attempts"] == 1 and "paper jam" in g["last_error"]
    env.run_worker_until_idle()
    g = env.get(job)
    assert g["state"] == "COMPLETED" and g["attempts"] == 2


def test_gives_up_after_max_attempts_then_staff_retry(env):
    job = env.paid_job()
    for _ in range(env.settings.print_max_attempts):
        env.ctx.printer.fail_next = PrintError("offline", retryable=True)
        env.worker.run_once()
    assert states(env, job) == "FAILED"
    st = env.staff("op", "operator12345")
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/retry", headers=st, json={"note": "fixed cable"})
    assert r.json()["state"] == "QUEUED"
    env.run_worker_until_idle()
    assert states(env, job) == "COMPLETED"


def test_unknown_outcome_is_uncertain_not_silently_reprinted(env):
    job = env.paid_job()
    env.ctx.printer.fail_next = PrintError("timeout talking to CUPS", retryable=False)
    env.worker.run_once()
    assert states(env, job) == "UNCERTAIN"
    assert env.worker.run_once() is False            # NOT auto-reprinted (could double print)
    st = env.staff("op", "operator12345")
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/complete", headers=st, json={"note": "paper is in tray"})
    assert r.json()["state"] == "COMPLETED"


def test_crash_mid_print_becomes_uncertain_via_lease(env):
    job = env.paid_job()
    with env.ctx.db.session() as db:             # simulate: worker claimed the job then the process died
        from smartprint.server.services import jobs
        jobs.claim_next(db, env.ctx)
    assert states(env, job) == "PRINTING"
    with env.ctx.db.session() as db:
        db.get(Job, job["id"]).lease_expires_at = utcnow() - timedelta(seconds=1)
    counts = env.worker.run_janitor()
    assert counts["uncertain"] == 1 and states(env, job) == "UNCERTAIN"
    assert env.worker.run_janitor()["uncertain"] == 0       # idempotent


def test_late_result_after_recovery_is_logged_not_applied(env):
    job = env.paid_job()
    with env.ctx.db.session() as db:
        from smartprint.server.services import jobs
        jobs.claim_next(db, env.ctx)
        db.get(Job, job["id"]).lease_expires_at = utcnow() - timedelta(seconds=1)
    env.worker.run_janitor()
    with env.ctx.db.session() as db:
        from smartprint.server.services import jobs
        jobs.finish_print(db, env.ctx, job["id"], "late-ref", None)
    assert states(env, job) == "UNCERTAIN"


def test_expired_payment_returns_job_to_configured(env):
    job = env.upload()
    env.configure(job)
    env.pay_online(job, simulate=False)
    with env.ctx.db.session() as db:
        for p in db.query(Payment).all():
            p.expires_at = utcnow() - timedelta(seconds=1)
    env.worker.run_janitor()
    g = env.get(job)
    assert g["state"] == "CONFIGURED" and g["payment"]["state"] == "EXPIRED"


def test_lost_webhook_is_reconciled_by_asking_the_provider(env):
    job = env.upload()
    env.configure(job)
    pay = env.pay_online(job, simulate=False)["payment"]
    body, headers = env.ctx.provider.simulate_payment(pay["reference"], pay["amount_minor"], pay["currency"], "t")
    env.ctx.provider.parse_webhook(headers, body)      # provider knows it is paid, but our server never got the call
    assert states(env, job) == "AWAITING_PAYMENT"
    assert env.worker.run_janitor()["payments_reconciled"] == 1
    assert states(env, job) == "QUEUED"


def test_idle_jobs_expire(env):
    job = env.upload()
    with env.ctx.db.session() as db:
        db.get(Job, job["id"]).expires_at = utcnow() - timedelta(seconds=1)
    env.worker.run_janitor()
    assert states(env, job) == "EXPIRED"


def test_illegal_transitions_are_impossible(env):
    from smartprint.server.services import jobs
    from smartprint.shared.enums import JobState
    job = env.paid_job()
    env.run_worker_until_idle()
    with env.ctx.db.session() as db:
        j = db.get(Job, job["id"])
        for target in JobState:
            if target != JobState.COMPLETED:
                try:
                    jobs.transition(db, j, target, actor="test")
                except jobs.InvalidTransition:
                    continue
                raise AssertionError(f"COMPLETED -> {target} must be impossible")


def test_refund_flow_for_failed_job(env):
    job = env.paid_job()
    env.ctx.printer.fail_next = PrintError("dead", retryable=False)
    env.worker.run_once()
    st = env.staff("op", "operator12345")
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/refund-request", headers=st, json={"note": "printer broken"})
    assert r.json()["state"] == "REFUND_PENDING"
    r = env.client.post(f"/api/v1/staff/jobs/{job['id']}/actions/refund", headers=st, json={})
    assert r.json()["state"] == "REFUNDED" and r.json()["payment"]["state"] == "REFUNDED"
    stats = env.client.get("/api/v1/staff/stats", headers=st).json()
    assert stats["revenue_minor"] == 0 and stats["refunded_minor"] > 0


def test_queue_is_fifo(env):
    a, b = env.paid_job(), env.paid_job()
    env.worker.run_once()
    assert states(env, a) == "COMPLETED" and states(env, b) == "QUEUED"


# ----------------------------------------------------------------- audit chain
def test_audit_chain_verifies_after_full_flow(env):
    env.paid_job()
    env.run_worker_until_idle()
    st = env.staff()
    r = env.client.get("/api/v1/staff/audit/verify", headers=st).json()
    assert r["ok"] and r["checked"] >= 8


def test_audit_detects_edited_row(env):
    env.paid_job()
    with env.ctx.db.session() as db:
        ev = db.query(AuditEvent).filter(AuditEvent.action == "payment.verified").one()
        ev.data = {**ev.data, "amount_minor": 1}                      # attacker lowers the recorded amount
    r = env.client.get("/api/v1/staff/audit/verify", headers=env.staff()).json()
    assert not r["ok"] and "modified" in r["reason"]


def test_audit_detects_deleted_row(env):
    env.paid_job()
    with env.ctx.db.session() as db:
        ev = db.query(AuditEvent).filter(AuditEvent.action == "payment.requested").one()
        db.delete(ev)
    r = env.client.get("/api/v1/staff/audit/verify", headers=env.staff()).json()
    assert not r["ok"] and "gap" in r["reason"]


def test_audit_detects_truncated_tail(env):
    env.paid_job()
    with env.ctx.db.session() as db:
        last = db.query(AuditEvent).order_by(AuditEvent.seq.desc()).first()
        db.delete(last)
    assert not env.client.get("/api/v1/staff/audit/verify", headers=env.staff()).json()["ok"]


def test_audit_records_who_did_what(env):
    job = env.paid_job()
    evs = env.client.get(f"/api/v1/staff/audit?entity_id={job['id']}", headers=env.staff()).json()
    assert [e["action"] for e in reversed(evs)][:3] == ["job.created", "job.configured", "job.awaiting_payment"]
    assert evs[-1]["actor"] == "pc:PC-01"


def test_pricing_versions_are_immutable_history(env):
    st = env.staff()
    job = env.upload(pages=2)
    env.configure(job)                           # priced on v1
    cfg = env.client.get("/api/v1/admin/pricing", headers=st).json()[0]["config"]
    cfg["services"]["digital"]["A4"]["BW"] = 2000
    assert env.client.post("/api/v1/admin/pricing", headers=st, json={"config": cfg}).json()["version"] == 2
    assert env.get(job)["total_minor"] == 2000                    # already-quoted job unchanged
    new = env.upload(pages=2)
    assert env.configure(new)["total_minor"] == 4000              # new jobs use v2
    bad = {**cfg, "services": {}}
    assert env.client.post("/api/v1/admin/pricing", headers=st, json={"config": bad}).status_code == 422


def test_register_workstation_and_disable(env):
    st = env.staff()
    r = env.client.post("/api/v1/admin/workstations", headers=st, json={"name": "PC-09"})
    key = r.json()["api_key"]
    assert env.client.post("/api/v1/jobs/xerox", headers={"X-PC-Key": key}).status_code == 201
    env.client.post(f"/api/v1/admin/workstations/{r.json()['id']}/active/false", headers=st)
    assert env.client.post("/api/v1/jobs/xerox", headers={"X-PC-Key": key}).status_code == 401


def test_finished_job_files_are_purged_after_retention(env):
    from datetime import timedelta
    from smartprint.server.db import utcnow
    from smartprint.server.models import Job
    from smartprint.server.services import files
    job = env.upload(pages=2)
    env.configure(job, copies=1)
    j = env.pay_online(job, simulate=True)
    env.worker.run_once()
    assert env.get(job)["state"] == "COMPLETED"
    with env.ctx.db.session() as db:
        row = db.get(Job, job["id"])
        path = files.file_path(env.settings, row.stored_name)
        assert path.exists()                                   # still there right after printing
        assert env.worker.run_janitor()["files_purged"] == 0   # inside the retention window
        row.updated_at = utcnow() - timedelta(seconds=env.settings.file_retention_s + 5)
    assert env.worker.run_janitor()["files_purged"] == 1
    assert not path.exists()
    with env.ctx.db.session() as db:
        assert db.get(Job, job["id"]).stored_name is None
