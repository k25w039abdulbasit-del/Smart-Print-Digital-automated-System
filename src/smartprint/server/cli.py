"""`smartprint-server` command line: serve, create admin, register PCs, seed a demo shop."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .context import build_context
from .models import StaffUser, Workstation
from .security import hash_password, new_secret, sha256_hex
from .services import audit, jobs


def _ctx():
    s = get_settings()
    ctx = build_context(s)
    ctx.db.create_all() if s.is_dev else None
    with ctx.db.session() as db:
        jobs.ensure_default_price_list(db, s.currency)
    return ctx


def create_admin(ctx, username: str, password: str, role: str = "ADMIN") -> bool:
    with ctx.db.session() as db:
        if db.execute(select(StaffUser).where(StaffUser.username == username)).scalar_one_or_none():
            return False
        db.add(StaffUser(username=username, password_hash=hash_password(password), role=role))
        audit.record(db, actor="cli", action="staff.created", entity_type="staff", entity_id=username, data={"role": role})
        return True


def register_pc(ctx, name: str, printer: str | None = None) -> str | None:
    key = "spk_" + new_secret(24)
    with ctx.db.session() as db:
        if db.execute(select(Workstation).where(Workstation.name == name)).scalar_one_or_none():
            return None
        w = Workstation(name=name, key_hash=sha256_hex(key), printer_name=printer)
        db.add(w)
        db.flush()
        audit.record(db, actor="cli", action="workstation.registered", entity_type="workstation", entity_id=str(w.id), data={"name": name})
    return key


def seed_demo(ctx, data_dir: Path) -> dict:
    """Idempotent demo data: admin/operator accounts and PC-01..PC-04 (as on the slides)."""
    creds_file = data_dir / "demo_credentials.json"
    creds = json.loads(creds_file.read_text()) if creds_file.exists() else {"pcs": {}}
    create_admin(ctx, "admin", "admin12345", "ADMIN")
    create_admin(ctx, "operator", "operator12345", "OPERATOR")
    creds["staff"] = {"admin": "admin12345", "operator": "operator12345"}
    for i in range(1, 5):
        name = f"PC-{i:02d}"
        key = register_pc(ctx, name)
        if key:
            creds["pcs"][name] = key
    creds_file.parent.mkdir(parents=True, exist_ok=True)
    creds_file.write_text(json.dumps(creds, indent=2))
    return creds


def run_migrations() -> None:
    """`alembic upgrade head`, usable from a PyInstaller build (which has no alembic CLI)."""
    from alembic import command
    from alembic.config import Config
    candidates = [Path(getattr(sys, "_MEIPASS", "/nonexistent")), Path(__file__).resolve().parents[3], Path.cwd()]
    base = next((c for c in candidates if (c / "alembic.ini").exists()), None)   # bundle, repo checkout, or cwd (Docker)
    if base is None:
        raise SystemExit("alembic.ini not found - run from the project folder")
    cfg = Config(str(base / "alembic.ini"))
    cfg.set_main_option("script_location", str(base / "alembic"))
    command.upgrade(cfg, "head")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("smartprint-server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the API server")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8000)
    a = sub.add_parser("create-admin", help="create a staff account")
    a.add_argument("username"); a.add_argument("password"); a.add_argument("--role", default="ADMIN", choices=["ADMIN", "OPERATOR"])
    p = sub.add_parser("register-pc", help="register a shop PC and print its API key once")
    p.add_argument("name"); p.add_argument("--printer")
    sub.add_parser("seed-demo", help="admin/operator + PC-01..PC-04 for a demo (dev only)")
    sub.add_parser("verify-audit", help="verify the audit hash chain")
    sub.add_parser("migrate", help="create/upgrade the database schema (Alembic) - run on every deploy")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("smartprint.server.main:app_factory", factory=True, host=args.host, port=args.port)
        return 0
    if args.cmd == "migrate":
        run_migrations()
        print("database schema is up to date")
        return 0
    ctx = _ctx()
    if args.cmd == "create-admin":
        print("created" if create_admin(ctx, args.username, args.password, args.role) else "already exists")
    elif args.cmd == "register-pc":
        key = register_pc(ctx, args.name, args.printer)
        print(f"API key for {args.name} (shown once): {key}" if key else "workstation already exists")
    elif args.cmd == "seed-demo":
        if not ctx.settings.is_dev:
            print("seed-demo is dev-only", file=sys.stderr)
            return 2
        creds = seed_demo(ctx, ctx.settings.data_dir)
        print(json.dumps(creds, indent=2))
    elif args.cmd == "verify-audit":
        with ctx.db.session() as db:
            r = audit.verify_chain(db)
        print(f"audit chain OK ({r.checked} events)" if r.ok else f"AUDIT CHAIN BROKEN at seq {r.broken_at}: {r.reason}")
        return 0 if r.ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
