"""Relational model. Money is always integer minor units (1 PKR = 100)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..shared.enums import JobKind, JobState, PaymentState
from .db import Base, UTCDateTime, utcnow


class StaffUser(Base):
    __tablename__ = "staff_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="OPERATOR")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Workstation(Base):
    """A registered shop PC. It authenticates with an API key (stored hashed)."""
    __tablename__ = "workstations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    printer_name: Mapped[Optional[str]] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PriceList(Base):
    """Immutable, versioned. A job keeps pointing at the version that priced it."""
    __tablename__ = "price_lists"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)          # human code, e.g. SP-7K3Q
    kind: Mapped[str] = mapped_column(String(10), default=JobKind.DIGITAL.value)
    state: Mapped[str] = mapped_column(String(20), default=JobState.DRAFT.value, index=True)
    version_id: Mapped[int] = mapped_column(Integer, default=1)          # optimistic locking
    workstation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workstations.id"))
    token_hash: Mapped[str] = mapped_column(String(64))                  # hash of the secret job token

    # file (digital only) - always a normalised PDF on disk
    original_name: Mapped[Optional[str]] = mapped_column(String(255))
    stored_name: Mapped[Optional[str]] = mapped_column(String(80))
    file_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)           # pages in file / xerox originals

    settings: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    quote: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    price_list_id: Mapped[Optional[int]] = mapped_column(ForeignKey("price_lists.id"))
    total_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="PKR")

    # print authorisation (single use, bound to file + settings + price)
    auth_nonce: Mapped[Optional[str]] = mapped_column(String(32))
    auth_signature: Mapped[Optional[str]] = mapped_column(String(64))
    auth_consumed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    # execution
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)  # retry back-off
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    printer_ref: Mapped[Optional[str]] = mapped_column(String(128))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    staff_note: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    payments: Mapped[list["Payment"]] = relationship(back_populates="job", order_by="Payment.created_at")
    workstation: Mapped[Optional[Workstation]] = relationship()

    __mapper_args__ = {"version_id_col": version_id}
    __table_args__ = (Index("ix_jobs_state_created", "state", "created_at"),)

    @property
    def active_payment(self) -> Optional["Payment"]:
        for p in reversed(self.payments):
            if p.state in (PaymentState.PENDING.value, PaymentState.PAID.value, PaymentState.MISMATCH.value):
                return p
        return self.payments[-1] if self.payments else None


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(10))
    reference: Mapped[str] = mapped_column(String(40), unique=True)      # what the customer/provider sees
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(12), default=PaymentState.PENDING.value, index=True)
    provider_txn_id: Mapped[Optional[str]] = mapped_column(String(80))
    pay_payload: Mapped[Optional[str]] = mapped_column(Text)             # QR / deep link / instructions
    verified_by: Mapped[Optional[str]] = mapped_column(String(64))      # 'webhook:mock' / 'staff:alice'
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    paid_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    job: Mapped[Job] = relationship(back_populates="payments")
    # idempotency is enforced in the service for *live* requests only; a key may be reused after a failure
    __table_args__ = (Index("ix_payment_job_idem", "job_id", "idempotency_key"),)


class ProviderEvent(Base):
    """Webhook replay protection: every provider event id is processed at most once."""
    __tablename__ = "provider_events"
    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditEvent(Base):
    """Append-only, hash-chained log: editing or deleting any row breaks verification."""
    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(48), index=True)
    entity_type: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditHead(Base):
    """Single row, locked FOR UPDATE while appending so the chain stays linear."""
    __tablename__ = "audit_head"
    id: Mapped[int] = mapped_column(primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_hash: Mapped[str] = mapped_column(String(64), default="0" * 64)
