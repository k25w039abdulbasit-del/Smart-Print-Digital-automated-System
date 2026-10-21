"""Endpoints used by the shop PCs (customer flow: FILE -> SETTINGS -> PAY -> PRINT)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, Response
from sqlalchemy import select

from ...shared.enums import JobKind
from ..deps import current_workstation, get_ctx, load_owned_job
from ..models import AuditEvent, Workstation
from ..schemas import JobOut, PayRequest
from ..services import files, jobs, payments
from ..services.pricing import PrintSettings

router = APIRouter(tags=["kiosk"])


@router.get("/pricing")
def rate_card(request: Request, ws: Workstation = Depends(current_workstation)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        pl = jobs.active_price_list(db)
        return {"version": pl.version, "config": pl.config}


@router.post("/jobs/digital", response_model=JobOut, status_code=201)
def create_digital(request: Request, file: UploadFile = File(...), ws: Workstation = Depends(current_workstation)):
    ctx = get_ctx(request)
    limit = ctx.settings.max_upload_mb * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit + 1024 * 1024:
        raise HTTPException(413, f"File is larger than the {ctx.settings.max_upload_mb} MB limit")
    with ctx.db.session() as db:
        job, token = jobs.create_digital(db, ctx, ws, file.file, file.filename or "document")
        return JobOut.of(job, token)


@router.post("/jobs/xerox", response_model=JobOut, status_code=201)
def create_xerox(request: Request, ws: Workstation = Depends(current_workstation)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job, token = jobs.create_xerox(db, ctx, ws)
        return JobOut.of(job, token)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request, ws: Workstation = Depends(current_workstation),
            x_job_token: Optional[str] = Header(None)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        return JobOut.of(load_owned_job(db, ws, job_id, x_job_token))


@router.put("/jobs/{job_id}/settings", response_model=JobOut)
def put_settings(job_id: str, settings: PrintSettings, request: Request,
                 ws: Workstation = Depends(current_workstation), x_job_token: Optional[str] = Header(None)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job = load_owned_job(db, ws, job_id, x_job_token)
        jobs.configure(db, ctx, job, settings, actor=f"pc:{ws.name}")
        return JobOut.of(job)


@router.post("/jobs/{job_id}/pay", response_model=JobOut)
def pay(job_id: str, body: PayRequest, request: Request, ws: Workstation = Depends(current_workstation),
        x_job_token: Optional[str] = Header(None), idempotency_key: Optional[str] = Header(None)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job = load_owned_job(db, ws, job_id, x_job_token)
        payments.request_payment(db, ctx, job, body.method, idempotency_key, actor=f"pc:{ws.name}")
        return JobOut.of(job)


@router.post("/jobs/{job_id}/payment/withdraw", response_model=JobOut)
def withdraw_payment(job_id: str, request: Request, ws: Workstation = Depends(current_workstation),
                     x_job_token: Optional[str] = Header(None)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job = load_owned_job(db, ws, job_id, x_job_token)
        payments.withdraw_request(db, job, actor=f"pc:{ws.name}")
        return JobOut.of(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: str, request: Request, ws: Workstation = Depends(current_workstation),
           x_job_token: Optional[str] = Header(None)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job = load_owned_job(db, ws, job_id, x_job_token)
        jobs.cancel(db, job, actor=f"pc:{ws.name}", reason="cancelled by customer")
        return JobOut.of(job)


@router.get("/jobs/{job_id}/preview")
def preview(job_id: str, request: Request, page: int = 1, width: int = 520,
            ws: Workstation = Depends(current_workstation), x_job_token: Optional[str] = Header(None)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job = load_owned_job(db, ws, job_id, x_job_token)
        if job.kind != JobKind.DIGITAL.value or not job.stored_name:
            raise HTTPException(404, "No preview for this job")
        if not 1 <= page <= (job.page_count or 1):
            raise HTTPException(404, "No such page")
        png = files.render_preview_png(files.file_path(ctx.settings, job.stored_name), page - 1, min(max(width, 120), 1200))
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.get("/jobs/{job_id}/timeline")
def timeline(job_id: str, request: Request, ws: Workstation = Depends(current_workstation),
             x_job_token: Optional[str] = Header(None)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        job = load_owned_job(db, ws, job_id, x_job_token)
        evs = db.execute(select(AuditEvent).where(AuditEvent.entity_type == "job", AuditEvent.entity_id == job.id)
                         .order_by(AuditEvent.seq)).scalars().all()
        return [{"ts": e.ts, "to": e.data.get("to"), "reason": e.data.get("reason", "")} for e in evs if e.data.get("to")]


@router.post("/dev/payments/{reference}/simulate", include_in_schema=False)
def simulate_payment(reference: str, request: Request, amount_minor: Optional[int] = None,
                     ws: Workstation = Depends(current_workstation)):
    """DEV ONLY: pretends the provider confirmed the payment, via the real signed-webhook path."""
    ctx = get_ctx(request)
    if not ctx.settings.is_dev:
        raise HTTPException(404)
    from ..models import Payment
    with ctx.db.session() as db:
        pay = db.execute(select(Payment).where(Payment.reference == reference)).scalar_one_or_none()
        if pay is None:
            raise HTTPException(404, "Unknown payment reference")
        body, headers = ctx.provider.simulate_payment(reference, amount_minor if amount_minor is not None else pay.amount_minor,
                                                      pay.currency, pay.provider_txn_id or "txn")
        outcome = payments.handle_webhook(db, ctx, headers, body)
    return {"outcome": outcome}
