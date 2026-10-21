"""Authentication/authorisation dependencies for the three kinds of callers:
shop PCs (API key), the job's owner at the kiosk (job token) and staff (signed bearer)."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared.enums import Role
from .context import AppContext
from .db import utcnow
from .models import Job, StaffUser, Workstation
from .security import sha256_hex, verify_token
from .services import jobs


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def current_workstation(request: Request, x_pc_key: Optional[str] = Header(None)) -> Workstation:
    ctx = get_ctx(request)
    if not x_pc_key:
        raise HTTPException(401, "Missing X-PC-Key")
    with ctx.db.session() as db:
        ws = db.execute(select(Workstation).where(Workstation.key_hash == sha256_hex(x_pc_key))).scalar_one_or_none()
        if ws is None or not ws.active:
            raise HTTPException(401, "Unknown or disabled workstation")
        now = utcnow()
        if ws.last_seen_at is None or now - ws.last_seen_at > timedelta(seconds=30):
            ws.last_seen_at = now
        return ws


def load_owned_job(db: Session, ws: Workstation, job_id: str, token: Optional[str]) -> Job:
    """A kiosk may touch a job only with that job's secret token (no customer accounts)."""
    job = db.get(Job, job_id)
    if job is None or not token or not hmac.compare_digest(sha256_hex(token), job.token_hash):
        raise HTTPException(404, "Job not found")     # same answer for 'missing' and 'wrong token'
    return job


@dataclass
class Staff:
    id: int
    username: str
    role: Role

    def actor(self) -> str:
        return f"staff:{self.username}"


def current_staff(request: Request, authorization: Optional[str] = Header(None)) -> Staff:
    ctx = get_ctx(request)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Staff login required")
    body = verify_token(authorization[7:], ctx.settings.secret_key)
    if not body or body.get("typ") != "staff":
        raise HTTPException(401, "Session expired - please sign in again")
    with ctx.db.session() as db:
        u = db.get(StaffUser, body["uid"])
        if u is None or not u.active:
            raise HTTPException(401, "Account disabled")
        return Staff(u.id, u.username, Role(u.role))


def require_admin(staff: Staff = Depends(current_staff)) -> Staff:
    if staff.role != Role.ADMIN:
        raise HTTPException(403, "Administrator access required")
    return staff
