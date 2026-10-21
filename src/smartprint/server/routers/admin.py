"""Administrator-only: price lists, workstations, staff accounts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...shared.enums import Role
from ..deps import Staff, get_ctx, require_admin
from ..models import PriceList, StaffUser, Workstation
from ..schemas import PriceListIn, StaffIn, WorkstationIn
from ..security import hash_password, new_secret, sha256_hex
from ..services import audit, pricing

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/pricing")
def list_prices(request: Request, admin: Staff = Depends(require_admin)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        rows = db.execute(select(PriceList).order_by(PriceList.version.desc())).scalars().all()
        return [{"version": p.version, "active": p.active, "config": p.config, "created_by": p.created_by,
                 "created_at": p.created_at} for p in rows]


@router.post("/pricing", status_code=201)
def new_price_list(body: PriceListIn, request: Request, admin: Staff = Depends(require_admin)):
    """Publishing never edits history: it creates version N+1 and flips the active flag.
    Jobs already priced keep pointing at the version that priced them."""
    ctx = get_ctx(request)
    try:
        pricing.validate_price_config(body.config)
    except pricing.PricingError as e:
        raise HTTPException(422, str(e))
    with ctx.db.session() as db:
        for p in db.execute(select(PriceList).where(PriceList.active.is_(True))).scalars():
            p.active = False
        nxt = (db.execute(select(PriceList.version).order_by(PriceList.version.desc()).limit(1)).scalar() or 0) + 1
        pl = PriceList(version=nxt, active=True, config=body.config, created_by=admin.username)
        db.add(pl)
        db.flush()
        audit.record(db, actor=admin.actor(), action="pricing.published", entity_type="price_list",
                     entity_id=str(pl.id), data={"version": nxt})
        return {"version": nxt}


@router.get("/workstations")
def list_ws(request: Request, admin: Staff = Depends(require_admin)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        return [{"id": w.id, "name": w.name, "printer_name": w.printer_name, "active": w.active,
                 "last_seen_at": w.last_seen_at} for w in db.execute(select(Workstation).order_by(Workstation.name)).scalars()]


@router.post("/workstations", status_code=201)
def register_ws(body: WorkstationIn, request: Request, admin: Staff = Depends(require_admin)):
    ctx = get_ctx(request)
    key = "spk_" + new_secret(24)
    with ctx.db.session() as db:
        w = Workstation(name=body.name, key_hash=sha256_hex(key), printer_name=body.printer_name)
        db.add(w)
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "A workstation with that name already exists")
        audit.record(db, actor=admin.actor(), action="workstation.registered", entity_type="workstation",
                     entity_id=str(w.id), data={"name": w.name})
        return {"id": w.id, "name": w.name, "api_key": key,
                "note": "Shown once. Store it in the kiosk's client.json."}


@router.post("/workstations/{ws_id}/active/{value}")
def set_ws_active(ws_id: int, value: bool, request: Request, admin: Staff = Depends(require_admin)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        w = db.get(Workstation, ws_id)
        if w is None:
            raise HTTPException(404)
        w.active = value
        audit.record(db, actor=admin.actor(), action="workstation.enabled" if value else "workstation.disabled",
                     entity_type="workstation", entity_id=str(w.id), data={"name": w.name})
        return {"id": w.id, "active": w.active}


@router.get("/staff")
def list_staff(request: Request, admin: Staff = Depends(require_admin)):
    ctx = get_ctx(request)
    with ctx.db.session() as db:
        return [{"id": u.id, "username": u.username, "role": u.role, "active": u.active}
                for u in db.execute(select(StaffUser).order_by(StaffUser.username)).scalars()]


@router.post("/staff", status_code=201)
def create_staff(body: StaffIn, request: Request, admin: Staff = Depends(require_admin)):
    ctx = get_ctx(request)
    if body.role not in {r.value for r in Role}:
        raise HTTPException(422, "role must be OPERATOR or ADMIN")
    with ctx.db.session() as db:
        u = StaffUser(username=body.username, password_hash=hash_password(body.password), role=body.role)
        db.add(u)
        try:
            db.flush()
        except IntegrityError:
            raise HTTPException(409, "Username already exists")
        audit.record(db, actor=admin.actor(), action="staff.created", entity_type="staff", entity_id=u.username,
                     data={"role": u.role})
        return {"id": u.id, "username": u.username, "role": u.role}
