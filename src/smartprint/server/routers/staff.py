"""Staff console API: queue control, xerox desk, refunds, stats, audit."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select

from ...shared.enums import ATTENTION_STATES, JobKind, JobState, PaymentState, TERMINAL_STATES
from ..db import utcnow
from ..deps import Staff, current_staff, get_ctx
from ..models import AuditEvent, Job, Payment, StaffUser, Workstation
from ..schemas import ActionBody, AuditOut, JobOut, StaffLogin
from ..security import sign_token, verify_password
from ..services import audit, jobs, payments

router = APIRouter(prefix="/staff", tags=["staff"])


@router.post("/login")
def login(body: StaffLogin, request: Request):
    ctx = get_ctx(request)
    key = f"{body.username.lower()}|{request.client.host if request.client else '-'}"
    if ctx.throttle.blocked(key):
        raise HTTPException(429, "Too many failed attempts. Wait a minute and try again.")
    with ctx.db.session() as db:
        u = db.execute(select(StaffUser).where(StaffUser.username == body.username)).scalar_one_or_none()
        ok = bool(u and u.active and verify_password(body.password, u.password_hash))
        audit.record(db, actor=f"staff:{body.username}", action="staff.login" if ok else "staff.login_failed",
                     entity_type="staff", entity_id=body.username, data={})
        if not ok:
            ctx.throttle.failure(key)
            db.commit()
            raise HTTPException(401, "Wrong username or password")
        ctx.throttle.success(key)
        token = sign_token({"typ": "staff", "uid": u.id, "role": u.role}, ctx.settings.secret_key,
                           ctx.settings.staff_token_ttl_s)
        return {"token": token, "username": u.username, "role": u.role}


@router.get("/me")
def me(staff: Staff = Depends(current_staff)):
    return {"username": staff.username, "role": staff.role.value}


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(request: Request, state: Optional[str] = None, kind: Optional[str] = None,
              q: Optional[str] = None, active_only: bool = False, limit: int = 100,
              staff: Staff = Depends(current_staff)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        stmt = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 500))
        if state:
            stmt = stmt.where(Job.state.in_(state.split(",")))
        if kind:
            stmt = stmt.where(Job.kind == kind)
        if active_only:
            stmt = stmt.where(Job.state.notin_([s.value for s in TERMINAL_STATES]))
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Job.code.ilike(like), Job.original_name.ilike(like)))
        return [JobOut.of(j) for j in db.execute(stmt).scalars()]


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_detail(job_id: str, request: Request, staff: Staff = Depends(current_staff)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        return JobOut.of(jobs.get_job(db, job_id))


@router.post("/jobs/{job_id}/actions/{action}", response_model=JobOut)
def job_action(job_id: str, action: str, body: ActionBody, request: Request, staff: Staff = Depends(current_staff)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job = jobs.get_job(db, job_id)
        a = staff.actor()
        if action == "confirm-cash":
            if body.amount_minor is None:
                raise HTTPException(422, "amount_minor is required: enter the cash you received")
            outcome = payments.confirm_counter(db, ctx, job, body.amount_minor, staff.username)
            if outcome == "mismatch":
                db.commit()
                raise HTTPException(409, f"Amount received does not match the quote ({job.total_minor / 100:.0f}). Payment held for review.")
        elif action == "retry":
            jobs.staff_retry(db, ctx, job, a, body.note)
        elif action == "complete":
            jobs.staff_complete(db, job, a, body.note)
        elif action == "refund-request":
            jobs.staff_cancel_paid(db, job, a, body.note)
        elif action == "refund":
            payments.refund(db, ctx, job, staff.username, body.note)
        elif action == "note":
            job.staff_note = body.note
            audit.record(db, actor=a, action="job.note", entity_type="job", entity_id=job.id, data={"note": body.note})
        else:
            raise HTTPException(404, f"Unknown action '{action}'")
        return JobOut.of(job)


@router.get("/stats")
def stats(request: Request, staff: Staff = Depends(current_staff)):
    ctx = get_ctx(request)
    local_now = datetime.now().astimezone()
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    with ctx.db.session() as db:
        by_state = dict(db.execute(select(Job.state, func.count()).group_by(Job.state)).all())
        paid = db.execute(select(Payment).where(Payment.paid_at >= start)).scalars().all()
        gross = sum(p.amount_minor for p in paid if p.state in (PaymentState.PAID.value, PaymentState.REFUNDED.value))
        refunded = sum(p.amount_minor for p in paid if p.state == PaymentState.REFUNDED.value)
        done = db.execute(select(Job).where(Job.state == JobState.COMPLETED.value, Job.completed_at >= start)).scalars().all()
        sides = sum((j.quote or {}).get("sides", 0) for j in done)
        by_method: dict[str, int] = {}
        for p in paid:
            if p.state == PaymentState.PAID.value:
                by_method[p.method] = by_method.get(p.method, 0) + p.amount_minor
        hourly = [0] * 24
        for p in paid:
            if p.state == PaymentState.PAID.value and p.paid_at:
                hourly[p.paid_at.astimezone().hour] += p.amount_minor
        attention = sum(c for s, c in by_state.items() if s in {x.value for x in ATTENTION_STATES})
        return {"currency": ctx.settings.currency, "revenue_minor": gross - refunded, "refunded_minor": refunded,
                "jobs_completed": len(done), "sides_printed": sides, "by_state": by_state,
                "by_method": by_method, "hourly_revenue": hourly, "attention": attention,
                "digital_done": sum(1 for j in done if j.kind == JobKind.DIGITAL.value),
                "xerox_done": sum(1 for j in done if j.kind == JobKind.XEROX.value)}


@router.get("/audit", response_model=list[AuditOut])
def audit_log(request: Request, limit: int = 200, before_seq: Optional[int] = None,
              entity_id: Optional[str] = None, staff: Staff = Depends(current_staff)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        stmt = select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(min(limit, 1000))
        if before_seq:
            stmt = stmt.where(AuditEvent.seq < before_seq)
        if entity_id:
            stmt = stmt.where(AuditEvent.entity_id == entity_id)
        return [AuditOut.model_validate(e) for e in db.execute(stmt).scalars()]


@router.get("/audit/verify")
def audit_verify(request: Request, staff: Staff = Depends(current_staff)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        r = audit.verify_chain(db)
        return {"ok": r.ok, "checked": r.checked, "broken_at": r.broken_at, "reason": r.reason}


@router.get("/system")
def system(request: Request, staff: Staff = Depends(current_staff)):
    ctx = get_ctx(request)
    ok, detail = ctx.printer.health()
    worker = getattr(request.app.state, "worker", None)
    cutoff = utcnow() - timedelta(minutes=2)
    with ctx.db.session() as db:
        pcs = db.execute(select(Workstation).order_by(Workstation.name)).scalars().all()
        return {"printer": {"backend": ctx.printer.name, "ok": ok, "detail": detail},
                "worker_alive": bool(worker and worker.alive), "environment": ctx.settings.environment,
                "xerox_automation": ctx.xerox.supports_automation,
                "workstations": [{"name": w.name, "active": w.active, "online": bool(w.last_seen_at and w.last_seen_at > cutoff),
                                  "last_seen_at": w.last_seen_at} for w in pcs]}
