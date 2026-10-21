"""API contracts (what crosses the wire). The client imports none of this - it only
speaks JSON - so server internals can change without breaking installed kiosks."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..shared.enums import PaymentMethod, STATE_LABELS, JobState
from .models import AuditEvent, Job, Payment
from .services.pricing import PrintSettings


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    method: str
    provider: str
    reference: str
    amount_minor: int
    currency: str
    state: str
    pay_payload: Optional[str]
    verified_by: Optional[str] = None
    expires_at: Optional[datetime]
    paid_at: Optional[datetime]


class JobOut(BaseModel):
    id: str
    code: str
    kind: str
    state: str
    state_label: str
    original_name: Optional[str]
    page_count: Optional[int]
    file_size: Optional[int]
    settings: Optional[dict[str, Any]]
    quote: Optional[dict[str, Any]]
    total_minor: int
    currency: str
    payment: Optional[PaymentOut]
    attempts: int
    last_error: Optional[str]
    staff_note: Optional[str]
    workstation: Optional[str]
    created_at: datetime
    updated_at: datetime
    access_token: Optional[str] = None      # only present in the creation response

    @classmethod
    def of(cls, job: Job, token: Optional[str] = None) -> "JobOut":
        pay = job.active_payment
        return cls(
            id=job.id, code=job.code, kind=job.kind, state=job.state,
            state_label=STATE_LABELS[JobState(job.state)], original_name=job.original_name,
            page_count=job.page_count, file_size=job.file_size, settings=job.settings, quote=job.quote,
            total_minor=job.total_minor, currency=job.currency,
            payment=PaymentOut.model_validate(pay) if pay else None,
            attempts=job.attempts, last_error=job.last_error, staff_note=job.staff_note,
            workstation=job.workstation.name if job.workstation else None,
            created_at=job.created_at, updated_at=job.updated_at, access_token=token)


class XeroxCreate(BaseModel):
    pass


class PayRequest(BaseModel):
    method: PaymentMethod = PaymentMethod.ONLINE


class StaffLogin(BaseModel):
    username: str
    password: str


class ActionBody(BaseModel):
    note: str = ""
    amount_minor: Optional[int] = None


class PriceListIn(BaseModel):
    config: dict[str, Any]


class WorkstationIn(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    printer_name: Optional[str] = None


class StaffIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = "OPERATOR"


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    ts: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    data: dict[str, Any]
    hash: str
