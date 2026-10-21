"""Payment orchestration: request -> verify -> (late/mismatch handling) -> refund.

Invariant: a job reaches the print queue only through `apply_success`, and only when
the verified amount + currency equal the amount quoted for that job.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Mapping, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...shared.enums import JobState, PaymentMethod, PaymentState
from ..adapters.payment import ProviderEvent, WebhookInvalid
from ..context import AppContext
from ..db import utcnow
from ..models import Job, Payment, ProviderEvent as ProviderEventRow
from . import audit, jobs

S = JobState
PS = PaymentState


def _reference() -> str:
    return "PAY-" + secrets.token_hex(5).upper()


# ---------------------------------------------------------------- request
def request_payment(db: Session, ctx: AppContext, job: Job, method: PaymentMethod,
                    idempotency_key: Optional[str], actor: str) -> Payment:
    state = JobState(job.state)
    if any(p.state == PS.MISMATCH.value for p in job.payments):
        raise jobs.InvalidTransition("A payment for this job needs staff review before you can pay again")
    if state == S.AWAITING_PAYMENT:
        # Idempotent: retrying the same request (or re-opening the screen) returns the live payment.
        live = next((p for p in job.payments if p.state == PS.PENDING.value), None)
        if live and (live.idempotency_key == idempotency_key or live.method == method.value):
            return live
        if live:
            jobs.cancel_pending_payments(job, actor, db, PS.CANCELLED)
        jobs.transition(db, job, S.CONFIGURED, actor=actor, reason="payment method changed")
        state = S.CONFIGURED
    if state != S.CONFIGURED:
        raise jobs.InvalidTransition(f"Cannot request payment while job is {state.value}")
    if job.total_minor <= 0 or not job.quote:
        raise jobs.Invalid("Job has not been priced")

    reference = _reference()
    payload, txn = "", None
    if method == PaymentMethod.ONLINE:
        charge = ctx.provider.create_charge(reference, job.total_minor, job.currency, ctx.settings.payment_ttl_s)
        payload, txn = charge.payload, charge.txn_id
        provider = ctx.provider.name
    else:
        payload = f"Pay {job.total_minor / 100:.0f} {job.currency} at the counter. Quote code {job.code}."
        provider = "counter"
    pay = Payment(job_id=job.id, provider=provider, method=method.value, reference=reference,
                  idempotency_key=idempotency_key, amount_minor=job.total_minor, currency=job.currency,
                  provider_txn_id=txn, pay_payload=payload,
                  expires_at=utcnow() + timedelta(seconds=ctx.settings.payment_ttl_s))
    db.add(pay)
    job.payments.append(pay)
    db.flush()
    audit.record(db, actor=actor, action="payment.requested", entity_type="payment", entity_id=str(pay.id),
                 data={"job": job.code, "reference": reference, "method": method.value,
                       "amount_minor": pay.amount_minor, "currency": pay.currency})
    job.expires_at = pay.expires_at + timedelta(seconds=60)
    jobs.transition(db, job, S.AWAITING_PAYMENT, actor=actor, reference=reference)
    return pay


# ---------------------------------------------------------------- verification
def apply_success(db: Session, ctx: AppContext, pay: Payment, amount_minor: int, currency: str,
                  txn_id: Optional[str], verified_by: str) -> str:
    """Single entry point for 'money arrived'. Idempotent. Returns an outcome label."""
    job = pay.job
    if pay.state == PS.PAID.value:
        return "duplicate"
    if pay.state in (PS.REFUNDED.value,):
        return "ignored"

    if amount_minor != pay.amount_minor or currency != pay.currency:
        pay.state, pay.verified_by = PS.MISMATCH.value, verified_by
        audit.record(db, actor=verified_by, action="payment.mismatch", entity_type="payment", entity_id=str(pay.id),
                     data={"job": job.code, "expected": pay.amount_minor, "received": amount_minor,
                           "currency": currency, "expected_currency": pay.currency})
        return "mismatch"

    pay.state, pay.verified_by, pay.paid_at = PS.PAID.value, verified_by, utcnow()
    pay.provider_txn_id = txn_id or pay.provider_txn_id
    audit.record(db, actor=verified_by, action="payment.verified", entity_type="payment", entity_id=str(pay.id),
                 data={"job": job.code, "amount_minor": amount_minor, "currency": currency, "txn": txn_id})

    if JobState(job.state) == S.AWAITING_PAYMENT:
        jobs.on_payment_verified(db, ctx, job, actor=verified_by)
        return "authorised"
    # Money arrived but the job moved on (expired / cancelled / payment was superseded).
    if JobState(job.state) in (S.EXPIRED, S.CANCELLED):
        jobs.transition(db, job, S.REFUND_PENDING, actor="system", reason="payment received after job ended")
        return "late_refund_pending"
    if JobState(job.state) in (S.CONFIGURED,):
        # customer switched method while paying the old one: honour the money, never double-charge
        jobs.transition(db, job, S.AWAITING_PAYMENT, actor="system", reason="paid via superseded request")
        jobs.on_payment_verified(db, ctx, job, actor=verified_by)
        return "authorised"
    return "recorded"


def handle_webhook(db: Session, ctx: AppContext, headers: Mapping[str, str], body: bytes) -> str:
    try:
        ev = ctx.provider.parse_webhook(headers, body)
    except WebhookInvalid as e:
        audit.record(db, actor="provider:" + ctx.provider.name, action="payment.webhook_rejected",
                     entity_type="payment", entity_id="-", data={"reason": str(e)})
        db.commit()
        raise
    # replay protection: same provider event is processed once
    try:
        with db.begin_nested():
            db.add(ProviderEventRow(event_id=ev.event_id))
            db.flush()
    except IntegrityError:
        return "replay"
    pay = db.execute(select(Payment).where(Payment.reference == ev.reference)).scalar_one_or_none()
    if pay is None:
        audit.record(db, actor="provider:" + ctx.provider.name, action="payment.unknown_reference",
                     entity_type="payment", entity_id="-", data={"reference": ev.reference})
        return "unknown"
    if ev.status != "succeeded":
        if pay.state == PS.PENDING.value:
            pay.state = PS.FAILED.value
            audit.record(db, actor="provider:" + ctx.provider.name, action="payment.failed", entity_type="payment",
                         entity_id=str(pay.id), data={"job": pay.job.code})
            # let the customer try again
            if JobState(pay.job.state) == S.AWAITING_PAYMENT:
                jobs.transition(db, pay.job, S.CONFIGURED, actor="system", reason="payment failed")
        return "failed"
    return apply_success(db, ctx, pay, ev.amount_minor, ev.currency, ev.txn_id, f"webhook:{ctx.provider.name}")


def confirm_counter(db: Session, ctx: AppContext, job: Job, received_minor: int, staff: str) -> str:
    """Staff took cash at the desk. The amount they key in must equal the quote."""
    pay = next((p for p in job.payments if p.state == PS.PENDING.value and p.method == PaymentMethod.COUNTER.value), None)
    if pay is None:
        raise jobs.InvalidTransition("No pending counter payment for this job")
    return apply_success(db, ctx, pay, received_minor, pay.currency, None, f"staff:{staff}")


# ---------------------------------------------------------------- reconcile / expire / refund
def reconcile_pending(db: Session, ctx: AppContext) -> int:
    """Ask the provider about payments we never heard back on (lost webhook)."""
    n = 0
    for pay in db.execute(select(Payment).where(Payment.state == PS.PENDING.value,
                                                Payment.method == PaymentMethod.ONLINE.value)).scalars():
        ev = ctx.provider.query(pay.reference)
        if ev and ev.status == "succeeded":
            apply_success(db, ctx, pay, ev.amount_minor, ev.currency, ev.txn_id, f"reconcile:{ctx.provider.name}")
            n += 1
    return n


def expire_pending(db: Session, ctx: AppContext) -> int:
    now = utcnow()
    n = 0
    for pay in db.execute(select(Payment).where(Payment.state == PS.PENDING.value, Payment.expires_at < now)).scalars():
        pay.state = PS.EXPIRED.value
        audit.record(db, actor="system", action="payment.expired", entity_type="payment", entity_id=str(pay.id),
                     data={"job": pay.job.code, "reference": pay.reference})
        if JobState(pay.job.state) == S.AWAITING_PAYMENT:
            jobs.transition(db, pay.job, S.CONFIGURED, actor="system", reason="payment request expired")
        n += 1
    return n


def refund(db: Session, ctx: AppContext, job: Job, staff: str, note: str = "") -> Payment:
    if JobState(job.state) != S.REFUND_PENDING:
        raise jobs.InvalidTransition("Job is not waiting for a refund")
    pay = next((p for p in reversed(job.payments) if p.state in (PS.PAID.value, PS.MISMATCH.value)), None)
    if pay is None:
        raise jobs.Invalid("No payment to refund")
    if pay.method == PaymentMethod.ONLINE.value and pay.state == PS.PAID.value and pay.provider_txn_id:
        if not ctx.provider.refund(pay.provider_txn_id, pay.amount_minor):
            raise jobs.JobError("Provider refused the refund; try again or refund manually")
    pay.state, pay.refunded_at = PS.REFUNDED.value, utcnow()
    audit.record(db, actor=f"staff:{staff}", action="payment.refunded", entity_type="payment", entity_id=str(pay.id),
                 data={"job": job.code, "amount_minor": pay.amount_minor, "note": note})
    jobs.transition(db, job, S.REFUNDED, actor=f"staff:{staff}", reason=note or "refund completed")
    return pay


def withdraw_request(db: Session, job: Job, actor: str) -> None:
    """Customer goes back to 'change options': drop the unpaid request, price becomes editable again."""
    if JobState(job.state) != S.AWAITING_PAYMENT:
        raise jobs.InvalidTransition("There is no open payment request to withdraw")
    if any(p.state in (PS.PAID.value, PS.MISMATCH.value) for p in job.payments):
        raise jobs.InvalidTransition("Money for this job was already received; ask the staff")
    jobs.cancel_pending_payments(job, actor, db, PS.CANCELLED)
    jobs.transition(db, job, S.CONFIGURED, actor=actor, reason="customer is changing options")
