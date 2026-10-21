"""Job lifecycle: the explicit state machine plus every operation that moves a job.

Rules enforced here (not in the UI, not in the API layer):
  * a job can only move along `TRANSITIONS`; every move is written to the audit log
  * settings are frozen once a payment request exists (price == what was agreed)
  * printing needs a valid single-use authorisation bound to file + settings + amount
  * every failure path ends in a state a human can act on - nothing is silently lost
"""
from __future__ import annotations

import json
import secrets
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...shared.enums import ATTENTION_STATES, JobKind, JobState, PaymentState
from ..adapters.printer import PrintError, PrintRequest
from ..context import AppContext
from ..db import utcnow
from ..models import Job, Payment, PriceList, Workstation
from ..security import print_authorisation, sha256_hex
from . import audit, files, pricing
from .pricing import PrintSettings

S = JobState

TRANSITIONS: dict[JobState, set[JobState]] = {
    S.DRAFT: {S.CONFIGURED, S.CANCELLED, S.EXPIRED},
    S.CONFIGURED: {S.CONFIGURED, S.AWAITING_PAYMENT, S.CANCELLED, S.EXPIRED},
    S.AWAITING_PAYMENT: {S.CONFIGURED, S.PAID, S.CANCELLED, S.EXPIRED, S.REFUND_PENDING},
    S.PAID: {S.QUEUED, S.AWAITING_STAFF, S.REFUND_PENDING},
    S.QUEUED: {S.PRINTING, S.FAILED, S.REFUND_PENDING},
    S.PRINTING: {S.COMPLETED, S.FAILED, S.UNCERTAIN, S.QUEUED},
    S.AWAITING_STAFF: {S.COMPLETED, S.REFUND_PENDING},
    S.FAILED: {S.QUEUED, S.REFUND_PENDING},
    S.UNCERTAIN: {S.QUEUED, S.COMPLETED, S.REFUND_PENDING},
    S.CANCELLED: {S.REFUND_PENDING},   # only reachable if money arrived after cancelling
    S.EXPIRED: {S.REFUND_PENDING},     # late payment on an expired job
    S.REFUND_PENDING: {S.REFUNDED},
    S.COMPLETED: set(),
    S.REFUNDED: set(),
}


class JobError(Exception):
    status = 400


class NotFound(JobError):
    status = 404


class InvalidTransition(JobError):
    status = 409


class Invalid(JobError):
    status = 422


CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I


# ---------------------------------------------------------------- helpers
def transition(db: Session, job: Job, to: JobState, *, actor: str, reason: str = "", **extra) -> None:
    cur = JobState(job.state)
    if to not in TRANSITIONS[cur]:
        raise InvalidTransition(f"Job {job.code}: {cur.value} -> {to.value} is not allowed")
    job.state = to.value
    job.updated_at = utcnow()
    if to == S.COMPLETED:
        job.completed_at = job.updated_at
    audit.record(db, actor=actor, action=f"job.{to.value.lower()}", entity_type="job", entity_id=job.id,
                 data={"code": job.code, "from": cur.value, "to": to.value, "reason": reason, **extra})


def get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFound("Job not found")
    return job


def get_job_by_code(db: Session, code: str) -> Job:
    job = db.execute(select(Job).where(Job.code == code.strip().upper())).scalar_one_or_none()
    if job is None:
        raise NotFound("Job not found")
    return job


def active_price_list(db: Session) -> PriceList:
    pl = db.execute(select(PriceList).where(PriceList.active.is_(True))).scalar_one_or_none()
    if pl is None:
        raise JobError("No active price list")
    return pl


def ensure_default_price_list(db: Session, currency: str) -> PriceList:
    pl = db.execute(select(PriceList).where(PriceList.active.is_(True))).scalar_one_or_none()
    if pl:
        return pl
    cfg = {**pricing.DEFAULT_PRICE_CONFIG, "currency": currency}
    pricing.validate_price_config(cfg)
    nxt = (db.execute(select(PriceList.version).order_by(PriceList.version.desc()).limit(1)).scalar() or 0) + 1
    pl = PriceList(version=nxt, active=True, config=cfg, created_by="system")
    db.add(pl)
    db.flush()
    audit.record(db, actor="system", action="pricing.created", entity_type="price_list", entity_id=str(pl.id),
                 data={"version": pl.version})
    return pl


def _new_code(db: Session) -> str:
    for _ in range(20):
        code = "SP-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        if db.execute(select(Job.id).where(Job.code == code)).first() is None:
            return code
    raise JobError("could not allocate job code")


def _new_job(db: Session, ctx: AppContext, kind: JobKind, ws: Optional[Workstation]) -> tuple[Job, str]:
    token = secrets.token_urlsafe(24)
    job = Job(id=str(uuid.uuid4()), code=_new_code(db), kind=kind.value, state=S.DRAFT.value,
              token_hash=sha256_hex(token), workstation_id=ws.id if ws else None,
              currency=ctx.settings.currency,
              expires_at=utcnow() + timedelta(seconds=ctx.settings.draft_ttl_s))
    db.add(job)
    db.flush()
    return job, token


# ---------------------------------------------------------------- creation / configuration
def create_digital(db: Session, ctx: AppContext, ws: Optional[Workstation], stream, filename: str) -> tuple[Job, str]:
    try:
        res = files.ingest(stream, filename, ctx.settings)
    except files.FileRejected as e:
        raise Invalid(str(e)) from e
    job, token = _new_job(db, ctx, JobKind.DIGITAL, ws)
    job.original_name, job.stored_name = res.display_name, res.stored_name
    job.file_sha256, job.file_size, job.page_count = res.sha256, res.size, res.pages
    audit.record(db, actor=_ws_actor(ws), action="job.created", entity_type="job", entity_id=job.id,
                 data={"code": job.code, "kind": "DIGITAL", "file": res.display_name, "pages": res.pages,
                       "sha256": res.sha256, "size": res.size})
    return job, token


def create_xerox(db: Session, ctx: AppContext, ws: Optional[Workstation]) -> tuple[Job, str]:
    job, token = _new_job(db, ctx, JobKind.XEROX, ws)
    audit.record(db, actor=_ws_actor(ws), action="job.created", entity_type="job", entity_id=job.id,
                 data={"code": job.code, "kind": "XEROX"})
    return job, token


def _ws_actor(ws: Optional[Workstation]) -> str:
    return f"pc:{ws.name}" if ws else "anonymous"


def configure(db: Session, ctx: AppContext, job: Job, settings: PrintSettings, actor: str) -> pricing.Quote:
    if JobState(job.state) not in (S.DRAFT, S.CONFIGURED):
        raise InvalidTransition("Settings are locked once payment has been requested")
    kind = JobKind(job.kind)
    pl = active_price_list(db)
    try:
        if kind == JobKind.XEROX:
            if not settings.originals:
                raise Invalid("Enter how many original pages you want to copy")
            quote = pricing.compute_quote(pl.config, kind, settings, settings.originals)
            job.page_count = settings.originals
        else:
            quote = pricing.compute_quote(pl.config, kind, settings, job.page_count or 0)
    except pricing.PricingError as e:
        raise Invalid(str(e)) from e
    job.settings = json.loads(settings.model_dump_json())
    job.quote = quote.to_dict()
    job.total_minor = quote.total_minor
    job.price_list_id = pl.id
    job.expires_at = utcnow() + timedelta(seconds=ctx.settings.draft_ttl_s)
    transition(db, job, S.CONFIGURED, actor=actor, total_minor=quote.total_minor, price_version=pl.version)
    return quote


# ---------------------------------------------------------------- cancel / expire
def cancel(db: Session, job: Job, actor: str, reason: str = "cancelled") -> None:
    state = JobState(job.state)
    if state not in (S.DRAFT, S.CONFIGURED, S.AWAITING_PAYMENT):
        raise InvalidTransition("Only unpaid jobs can be cancelled; paid jobs need a staff refund")
    cancel_pending_payments(job, actor, db, PaymentState.CANCELLED)
    transition(db, job, S.CANCELLED, actor=actor, reason=reason)


def cancel_pending_payments(job: Job, actor: str, db: Session, to: PaymentState) -> None:
    for p in job.payments:
        if p.state == PaymentState.PENDING.value:
            p.state = to.value
            audit.record(db, actor=actor, action=f"payment.{to.value.lower()}", entity_type="payment",
                         entity_id=str(p.id), data={"job": job.code, "reference": p.reference})


# ---------------------------------------------------------------- authorisation
def authorise(ctx: AppContext, job: Job) -> None:
    """Issue a fresh single-use print authorisation (called when payment is verified,
    or when a staff member deliberately re-authorises a reprint)."""
    assert job.file_sha256 and job.settings is not None
    job.auth_nonce = secrets.token_hex(12)
    job.auth_consumed_at = None
    job.auth_signature = print_authorisation(
        ctx.settings.secret_key, job_id=job.id, file_sha256=job.file_sha256, settings=job.settings,
        total_minor=job.total_minor, nonce=job.auth_nonce)


def authorisation_valid(ctx: AppContext, job: Job) -> bool:
    if not (job.auth_nonce and job.auth_signature and job.file_sha256 and job.settings is not None):
        return False
    good = print_authorisation(ctx.settings.secret_key, job_id=job.id, file_sha256=job.file_sha256,
                               settings=job.settings, total_minor=job.total_minor, nonce=job.auth_nonce)
    import hmac
    return hmac.compare_digest(good, job.auth_signature)


def on_payment_verified(db: Session, ctx: AppContext, job: Job, actor: str) -> None:
    """Payment is verified -> authorise -> hand over to the queue (digital) or staff (xerox)."""
    transition(db, job, S.PAID, actor=actor, total_minor=job.total_minor)
    if JobKind(job.kind) == JobKind.DIGITAL:
        authorise(ctx, job)
        transition(db, job, S.QUEUED, actor="system", reason="print authorised")
    else:
        transition(db, job, S.AWAITING_STAFF, actor="system", reason="xerox is staff-assisted")


# ---------------------------------------------------------------- print worker operations
def claim_next(db: Session, ctx: AppContext) -> Optional[tuple[Job, PrintRequest]]:
    now = utcnow()
    q = (select(Job).where(Job.state == S.QUEUED.value, Job.kind == JobKind.DIGITAL.value)
         .where((Job.not_before.is_(None)) | (Job.not_before <= now))
         .order_by(Job.created_at).limit(1).with_for_update(skip_locked=True))
    job = db.execute(q).scalar_one_or_none()
    if job is None:
        return None

    # --- security gates -------------------------------------------------
    path = files.file_path(ctx.settings, job.stored_name or "")
    problem = None
    if not authorisation_valid(ctx, job):
        problem = ("security.auth_invalid", "print authorisation missing or does not match job data")
    elif job.auth_consumed_at is not None:
        problem = ("security.auth_replayed", "print authorisation was already used")
    elif not path.exists() or files.sha256_file(path) != job.file_sha256:
        problem = ("security.file_tampered", "stored file does not match the paid file")
    if problem:
        event, reason = problem
        audit.record(db, actor="system", action=event, entity_type="job", entity_id=job.id,
                     data={"code": job.code, "reason": reason})
        job.last_error = reason
        transition(db, job, S.FAILED, actor="system", reason=reason)
        return None

    job.attempts += 1
    job.auth_consumed_at = now
    job.lease_expires_at = now + timedelta(seconds=ctx.settings.print_lease_s)
    transition(db, job, S.PRINTING, actor="system", attempt=job.attempts)

    st = job.settings or {}
    sel = pricing.parse_page_range(st.get("page_range", ""), job.page_count or 0)
    printer = job.workstation.printer_name if job.workstation and job.workstation.printer_name else ctx.settings.default_printer
    req = PrintRequest(job.id, job.code, job.attempts, path, printer, st.get("copies", 1), st.get("color", "BW"),
                       bool(st.get("duplex")), st.get("paper", "A4"), sel)
    return job, req


def finish_print(db: Session, ctx: AppContext, job_id: str, ref: Optional[str], error: Optional[PrintError]) -> None:
    job = get_job(db, job_id)
    if JobState(job.state) != S.PRINTING:      # e.g. janitor already marked it UNCERTAIN
        audit.record(db, actor="system", action="print.late_result", entity_type="job", entity_id=job.id,
                     data={"code": job.code, "state": job.state, "ok": error is None, "ref": ref})
        return
    job.lease_expires_at = None
    if error is None:
        job.printer_ref, job.last_error = ref, None
        transition(db, job, S.COMPLETED, actor="system", printer_ref=ref)
        return
    job.last_error = str(error)
    if error.retryable and job.attempts < ctx.settings.print_max_attempts:
        job.auth_consumed_at = None            # definitely nothing printed -> same authorisation may retry
        job.not_before = utcnow() + timedelta(seconds=ctx.settings.print_retry_backoff_s * job.attempts)
        transition(db, job, S.QUEUED, actor="system", reason=f"retry after error: {error}", attempt=job.attempts)
    elif error.retryable:
        transition(db, job, S.FAILED, actor="system", reason=f"gave up after {job.attempts} attempts: {error}")
    else:
        transition(db, job, S.UNCERTAIN, actor="system", reason=f"outcome unknown: {error}")


# ---------------------------------------------------------------- recovery (janitor)
def recover(db: Session, ctx: AppContext) -> dict[str, int]:
    """Idempotent sweep that turns every stuck/uncertain situation into an explicit state."""
    now = utcnow()
    counts = {"uncertain": 0, "expired": 0, "payments_expired": 0, "payments_reconciled": 0, "files_purged": 0}

    stuck = db.execute(select(Job).where(Job.state == S.PRINTING.value, Job.lease_expires_at < now)).scalars().all()
    for job in stuck:
        job.lease_expires_at = None
        job.last_error = "print worker stopped before reporting a result"
        transition(db, job, S.UNCERTAIN, actor="system", reason="lease expired while printing")
        counts["uncertain"] += 1

    from . import payments  # local import: payments depends on this module
    counts["payments_reconciled"] = payments.reconcile_pending(db, ctx)
    counts["payments_expired"] = payments.expire_pending(db, ctx)

    idle = db.execute(select(Job).where(Job.state.in_([S.DRAFT.value, S.CONFIGURED.value]),
                                        Job.expires_at < now)).scalars().all()
    for job in idle:
        transition(db, job, S.EXPIRED, actor="system", reason="idle timeout")
        counts["expired"] += 1

    counts["files_purged"] = purge_finished_files(db, ctx)
    return counts


def purge_finished_files(db: Session, ctx: AppContext) -> int:
    """Privacy: customer documents are deleted shortly after the job ends. Jobs that may still be
    reprinted (FAILED / UNCERTAIN / queued) keep their file."""
    cutoff = utcnow() - timedelta(seconds=ctx.settings.file_retention_s)
    done = [S.COMPLETED.value, S.CANCELLED.value, S.EXPIRED.value, S.REFUNDED.value]
    rows = db.execute(select(Job).where(Job.state.in_(done), Job.stored_name.is_not(None),
                                        Job.updated_at < cutoff)).scalars().all()
    for job in rows:
        files.file_path(ctx.settings, job.stored_name).unlink(missing_ok=True)
        audit.record(db, actor="system", action="file.purged", entity_type="job", entity_id=job.id,
                     data={"code": job.code, "sha256": job.file_sha256})
        job.stored_name = None
    return len(rows)


# ---------------------------------------------------------------- staff operations
def staff_retry(db: Session, ctx: AppContext, job: Job, actor: str, note: str = "") -> None:
    """FAILED/UNCERTAIN -> QUEUED with a *new* authorisation, recorded against a named person."""
    if JobState(job.state) not in (S.FAILED, S.UNCERTAIN):
        raise InvalidTransition("Only failed or uncertain jobs can be re-queued")
    authorise(ctx, job)
    job.attempts, job.not_before, job.last_error = 0, None, None
    transition(db, job, S.QUEUED, actor=actor, reason=note or "re-authorised by staff", reauthorised=True)


def staff_complete(db: Session, job: Job, actor: str, note: str = "") -> None:
    if JobState(job.state) not in (S.UNCERTAIN, S.AWAITING_STAFF):
        raise InvalidTransition("Job is not waiting for a staff completion")
    job.staff_note = note or job.staff_note
    transition(db, job, S.COMPLETED, actor=actor, reason=note or "confirmed by staff")


def staff_cancel_paid(db: Session, job: Job, actor: str, note: str = "") -> None:
    """Paid (or paid-late) job that will not be delivered -> money must go back."""
    if JobState(job.state) in (S.AWAITING_PAYMENT,):
        has_money = any(p.state in (PaymentState.PAID.value, PaymentState.MISMATCH.value) for p in job.payments)
        if not has_money:
            return cancel(db, job, actor, note or "cancelled by staff")
    job.staff_note = note or job.staff_note
    transition(db, job, S.REFUND_PENDING, actor=actor, reason=note or "refund requested")


def needs_attention(job: Job) -> bool:
    return JobState(job.state) in ATTENTION_STATES
