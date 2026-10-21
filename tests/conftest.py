from __future__ import annotations

import io
import os
import time

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from smartprint.server.cli import create_admin, register_pc
from smartprint.server.config import Settings
from smartprint.server.context import build_context
from smartprint.server.main import create_app
from smartprint.server.worker import PrintWorker


def make_pdf(pages: int = 3) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.drawString(100, 750, f"SmartPrint test page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


class Env:
    """A fully wired server with the background worker OFF so tests drive it deterministically."""

    def __init__(self, tmp_path):
        # SMARTPRINT_TEST_DB=postgresql://... runs the whole suite against PostgreSQL
        url = os.environ.get("SMARTPRINT_TEST_DB") or f"sqlite:///{tmp_path}/t.db"
        self.settings = Settings(environment="dev", database_url=url, data_dir=tmp_path,
                                 worker_enabled=False, print_retry_backoff_s=0.0, payment_ttl_s=600)
        self.ctx = build_context(self.settings)
        if os.environ.get("SMARTPRINT_TEST_DB"):
            from smartprint.server import models  # noqa: F401
            from smartprint.server.db import Base
            Base.metadata.drop_all(self.ctx.db.engine)
        self.app = create_app(ctx=self.ctx)
        self.client = TestClient(self.app)
        self.client.__enter__()                  # run lifespan (tables, price list)
        self.worker = PrintWorker(self.ctx)
        create_admin(self.ctx, "admin", "admin12345", "ADMIN")
        create_admin(self.ctx, "op", "operator12345", "OPERATOR")
        self.pc_key = register_pc(self.ctx, "PC-01")
        self.pc = {"X-PC-Key": self.pc_key}

    def staff(self, user="admin", pw="admin12345") -> dict:
        r = self.client.post("/api/v1/staff/login", json={"username": user, "password": pw})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def upload(self, pages=3, name="doc.pdf", data=None) -> dict:
        r = self.client.post("/api/v1/jobs/digital", headers=self.pc,
                             files={"file": (name, data or make_pdf(pages), "application/pdf")})
        assert r.status_code == 201, r.text
        return r.json()

    def h(self, job: dict) -> dict:
        return {**self.pc, "X-Job-Token": job["access_token"]}

    def configure(self, job: dict, **settings) -> dict:
        r = self.client.put(f"/api/v1/jobs/{job['id']}/settings", headers=self.h(job), json=settings)
        assert r.status_code == 200, r.text
        return r.json()

    def pay_online(self, job: dict, simulate=True) -> dict:
        r = self.client.post(f"/api/v1/jobs/{job['id']}/pay", headers={**self.h(job), "Idempotency-Key": "k1"},
                             json={"method": "ONLINE"})
        assert r.status_code == 200, r.text
        j = r.json()
        if simulate:
            s = self.client.post(f"/api/v1/dev/payments/{j['payment']['reference']}/simulate", headers=self.pc)
            assert s.status_code == 200, s.text
        return j

    def get(self, job: dict) -> dict:
        return self.client.get(f"/api/v1/jobs/{job['id']}", headers=self.h(job)).json()

    def run_worker_until_idle(self, limit=10):
        for _ in range(limit):
            if not self.worker.run_once():
                break

    def paid_job(self, pages=3, **settings) -> dict:
        job = self.upload(pages)
        self.configure(job, **settings)
        self.pay_online(job)
        return job

    def close(self):
        self.client.__exit__(None, None, None)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()
