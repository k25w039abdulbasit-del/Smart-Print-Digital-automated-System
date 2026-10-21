"""Tamper-evident audit trail (hash chain).

hash_n = SHA256(hash_{n-1} || canonical(seq, ts, actor, action, entity, data))
Any edit, deletion or reordering of a past row breaks every hash after it, which
`verify_chain` detects. Appending happens inside the caller's transaction, so an
audit record exists if and only if the business change it describes was committed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import AuditEvent, AuditHead
from ..security import sha256_hex

GENESIS = "0" * 64


def _canon(seq: int, ts, actor: str, action: str, etype: str, eid: str, data: dict) -> str:
    return json.dumps([seq, ts.isoformat(), actor, action, etype, eid, data],
                      sort_keys=True, separators=(",", ":"), default=str)


def record(db: Session, *, actor: str, action: str, entity_type: str, entity_id: str,
           data: Optional[dict[str, Any]] = None) -> AuditEvent:
    head = db.execute(select(AuditHead).where(AuditHead.id == 1).with_for_update()).scalar_one_or_none()
    if head is None:
        head = AuditHead(id=1, last_seq=0, last_hash=GENESIS)
        db.add(head)
        db.flush()
    seq = head.last_seq + 1
    ts = utcnow()
    data = json.loads(json.dumps(data or {}, default=str))   # force JSON-safe, same as stored
    h = sha256_hex(head.last_hash + _canon(seq, ts, actor, action, entity_type, entity_id, data))
    ev = AuditEvent(seq=seq, ts=ts, actor=actor, action=action, entity_type=entity_type,
                    entity_id=entity_id, data=data, prev_hash=head.last_hash, hash=h)
    db.add(ev)
    head.last_seq, head.last_hash = seq, h
    db.flush()
    return ev


@dataclass
class ChainReport:
    ok: bool
    checked: int
    broken_at: Optional[int] = None
    reason: str = ""


def verify_chain(db: Session) -> ChainReport:
    prev, expected_seq, n = GENESIS, 1, 0
    for ev in db.execute(select(AuditEvent).order_by(AuditEvent.seq)).scalars():
        if ev.seq != expected_seq:
            return ChainReport(False, n, ev.seq, f"gap: expected seq {expected_seq}")
        if ev.prev_hash != prev:
            return ChainReport(False, n, ev.seq, "prev_hash mismatch")
        calc = sha256_hex(prev + _canon(ev.seq, ev.ts, ev.actor, ev.action, ev.entity_type, ev.entity_id, ev.data))
        if calc != ev.hash:
            return ChainReport(False, n, ev.seq, "content hash mismatch (row was modified)")
        prev, expected_seq, n = ev.hash, expected_seq + 1, n + 1
    head = db.get(AuditHead, 1)
    if head is not None and (head.last_seq != n or head.last_hash != prev):
        return ChainReport(False, n, n + 1, "head mismatch (trailing rows deleted)")
    return ChainReport(True, n)
