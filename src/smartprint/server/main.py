"""FastAPI application factory (modular monolith: one deployable, clear internal modules)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from ..shared import API_PREFIX
from .config import Settings, get_settings
from .context import AppContext, build_context
from .routers import admin, kiosk, staff, webhooks
from .services import jobs
from .worker import PrintWorker

log = logging.getLogger("smartprint")


def create_app(settings: Optional[Settings] = None, ctx: Optional[AppContext] = None) -> FastAPI:
    ctx = ctx or build_context(settings or get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if ctx.settings.is_dev:
            ctx.db.create_all()            # prod uses `alembic upgrade head`
        with ctx.db.session() as db:
            jobs.ensure_default_price_list(db, ctx.settings.currency)
        worker = PrintWorker(ctx)
        app.state.worker = worker
        if ctx.settings.worker_enabled:
            worker.start()
        else:
            worker.run_janitor()
        log.info("SmartPrint server ready (%s, printer=%s)", ctx.settings.environment, ctx.printer.name)
        yield
        worker.stop()

    app = FastAPI(title="SmartPrint API", version="1.0.0", lifespan=lifespan,
                  description="Smart Digital Printing & Shop Automation")
    app.state.ctx = ctx

    @app.exception_handler(jobs.JobError)
    async def job_error(_: Request, exc: jobs.JobError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(StaleDataError)
    async def stale(_: Request, exc: StaleDataError):
        # two requests raced on the same job; the loser is told to retry (optimistic locking)
        return JSONResponse({"detail": "The job was changed by another request. Please retry."}, status_code=409)

    @app.get("/health", tags=["system"])
    def health():
        ok_db = True
        try:
            with ctx.db.session() as db:
                db.execute(__import__("sqlalchemy").text("select 1"))
        except Exception:  # noqa: BLE001
            ok_db = False
        printer_ok, detail = ctx.printer.health()
        worker = getattr(app.state, "worker", None)
        return {"status": "ok" if ok_db and printer_ok else "degraded", "database": ok_db,
                "printer": {"ok": printer_ok, "detail": detail},
                "worker": bool(worker and worker.alive) if ctx.settings.worker_enabled else "disabled"}

    for r in (kiosk.router, webhooks.router, staff.router, admin.router):
        app.include_router(r, prefix=API_PREFIX)
    return app


def app_factory() -> FastAPI:       # uvicorn --factory smartprint.server.main:app_factory
    return create_app()
