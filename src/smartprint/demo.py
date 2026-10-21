"""`smartprint-demo` - a narrated, self-checking tour of every promise on the project slides.

It boots a *real* SmartPrint server (HTTP, SQLite or the DB you point it at, background print
worker, spool printer) in a temp folder and drives it exactly like the shop PCs and the staff
console do. Every step asserts what it claims, so the demo doubles as an acceptance test:

    smartprint-demo            # run the tour, exit code 0 only if every check passed
    smartprint-demo up         # long-running demo shop on :8000 with sample jobs (then open the GUIs)
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import uvicorn
from reportlab.pdfgen import canvas
from sqlalchemy import text

from .server.adapters.payment import SIG_HEADER
from .server.adapters.printer import PrintError
from .server.cli import create_admin, register_pc, seed_demo
from .server.config import Settings
from .server.context import build_context
from .server.main import create_app
from .server.services import jobs as job_service

B, G, R, D, X = "\033[1m", "\033[32m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    B = G = R = D = X = ""


def sample_pdf(pages: int, title: str = "SmartPrint demo") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.setFont("Helvetica-Bold", 26)
        c.drawString(72, 760, f"{title} - page {i + 1} of {pages}")
        c.showPage()
    c.save()
    return buf.getvalue()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Shop:
    """A running server + convenience calls, shaped like the real clients."""

    def __init__(self, data_dir: Path, port: int, database_url: Optional[str] = None, quiet: bool = True):
        self.settings = Settings(environment="dev", data_dir=data_dir, worker_poll_s=0.1, worker_enabled=True,
                                 database_url=database_url or f"sqlite:///{data_dir}/demo.db",
                                 print_retry_backoff_s=0.2, janitor_interval_s=3600)
        self.ctx = build_context(self.settings)
        self.app = create_app(ctx=self.ctx)
        self.base = f"http://127.0.0.1:{port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port,
                                                    log_level="warning" if quiet else "info"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.http = httpx.Client(base_url=self.base + "/api/v1", timeout=30)

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @property
    def worker(self):
        return self.app.state.worker

    # --- calls
    def staff(self, user: str, pw: str) -> dict[str, str]:
        r = self.http.post("/staff/login", json={"username": user, "password": pw})
        r.raise_for_status()
        return {"Authorization": "Bearer " + r.json()["token"]}

    def upload(self, pc: dict, pdf: bytes, name: str = "report.pdf") -> dict:
        r = self.http.post("/jobs/digital", headers=pc, files={"file": (name, pdf, "application/pdf")})
        r.raise_for_status()
        return r.json()

    def configure(self, pc: dict, job: dict, **settings: Any) -> dict:
        r = self.http.put(f"/jobs/{job['id']}/settings", headers={**pc, "X-Job-Token": job["access_token"]}, json=settings)
        r.raise_for_status()
        return r.json()

    def pay(self, pc: dict, job: dict, method: str) -> dict:
        r = self.http.post(f"/jobs/{job['id']}/pay", headers={**pc, "X-Job-Token": job["access_token"], "Idempotency-Key": f"k-{time.time_ns()}"},
                           json={"method": method})
        r.raise_for_status()
        return r.json()

    def job(self, pc: dict, job: dict) -> dict:
        r = self.http.get(f"/jobs/{job['id']}", headers={**pc, "X-Job-Token": job["access_token"]})
        r.raise_for_status()
        return r.json()

    def webhook(self, reference: str, amount: int, cur: str = "PKR", event_id: Optional[str] = None,
                tamper_sig: bool = False) -> httpx.Response:
        body, headers = self.ctx.provider.simulate_payment(reference, amount, cur, "txn-" + reference, event_id=event_id)
        if tamper_sig:
            headers = {**headers, SIG_HEADER: "0" * 64}
        return self.http.post("/payments/webhook/mock", content=body, headers={**headers, "content-type": "application/json"})

    def wait_for(self, pc: dict, job: dict, states: set[str], timeout: float = 10) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            j = self.job(pc, job)
            if j["state"] in states:
                return j
            time.sleep(0.1)
        raise AssertionError(f"{job['code']} did not reach {states}; stuck in {j['state']}")


# ------------------------------------------------------------------ the narrated tour
class Tour:
    def __init__(self, shop: Shop):
        self.shop, self.n, self.checks = shop, 0, 0

    def section(self, title: str) -> None:
        print(f"\n{B}{title}{X}")

    def check(self, ok: bool, what: str, detail: str = "") -> None:
        self.checks += 1
        if not ok:
            print(f"  {R}✗ {what}{X} {detail}")
            raise AssertionError(what)
        print(f"  {G}✓{X} {what}" + (f"  {D}{detail}{X}" if detail else ""))

    def run(self, pcs: dict[str, dict], ops: dict, admin: dict) -> None:
        s, pc1, pc2 = self.shop, pcs["PC-01"], pcs["PC-02"]

        self.section("1  Digital print: FILE → SETTINGS → PAY → PRINT (slide 2, 4, 10)")
        job = s.upload(pc1, sample_pdf(10, "Thesis"), "thesis.pdf")
        self.check(job["state"] == "DRAFT" and job["page_count"] == 10, "file received & validated", f"{job['code']}, 10 pages")
        job_t = {**job}
        q = s.configure(pc1, job, copies=2, color="BW", duplex=True, paper="A4", page_range="1-5")
        self.check(q["state"] == "CONFIGURED" and q["quote"]["sides"] == 10, "server priced it (5 pages x 2 copies)",
                   f"PKR {q['total_minor'] / 100:.0f}: " + "; ".join(f"{l['label']} {l['qty']}x" for l in q["quote"]["lines"]))
        paid = s.pay(pc1, job_t, "ONLINE")
        time.sleep(0.6)
        self.check(s.job(pc1, job_t)["state"] == "AWAITING_PAYMENT", "payment requested - nothing prints yet")
        r = s.webhook(paid["payment"]["reference"], q["total_minor"])
        self.check(r.status_code == 200 and r.json()["outcome"] == "authorised", "provider webhook verified (HMAC) → payment PAID")
        done = s.wait_for(pc1, job_t, {"COMPLETED"})
        spool = next(s.settings.spool_dir.glob(f"{job['code']}-a*/output.pdf"))
        from pypdf import PdfReader
        self.check(len(PdfReader(str(spool)).pages) == 10, "printed exactly what was paid for", f"{len(PdfReader(str(spool)).pages)} sides in the spool")

        self.section("2  Payment control: verify before production (slide 3, 6)")
        j2 = s.upload(pc1, sample_pdf(3), "invoice.pdf")
        q2 = s.configure(pc1, j2, copies=1)
        ref2 = s.pay(pc1, j2, "ONLINE")["payment"]["reference"]
        bad = s.webhook(ref2, q2["total_minor"], tamper_sig=True)
        self.check(bad.status_code == 400, "webhook with a forged signature is rejected")
        short = s.webhook(ref2, q2["total_minor"] - 500)
        st = s.job(pc1, j2)
        self.check(st["payment"]["state"] == "MISMATCH" and st["state"] == "AWAITING_PAYMENT",
                   "wrong amount is held for staff - it can NOT trigger printing", f"outcome={short.json().get('outcome')}")
        j3 = s.upload(pc1, sample_pdf(2), "flyer.pdf")
        q3 = s.configure(pc1, j3, copies=1)
        ref3 = s.pay(pc1, j3, "ONLINE")["payment"]["reference"]
        body, hdr = s.ctx.provider.simulate_payment(ref3, q3["total_minor"], "PKR", "t3", event_id="evt_replay_demo")
        post = lambda: s.http.post("/payments/webhook/mock", content=body, headers={**hdr, "content-type": "application/json"})
        post()
        again = post()
        s.wait_for(pc1, j3, {"COMPLETED"})
        self.check(again.json()["outcome"] == "replay", "replayed webhook ignored (idempotent) - customer charged once")
        other = s.http.get(f"/jobs/{j3['id']}", headers={**pc2, "X-Job-Token": "guess"})
        self.check(other.status_code == 404, "another PC / a guessed token cannot see the job (no accounts, no leaks)")

        self.section("3  Xerox: physical workflow, staff-assisted (slide 6, 10)")
        x = s.http.post("/jobs/xerox", headers=pc2).json()
        xq = s.configure(pc2, x, originals=5, copies=3, color="BW", duplex=False, paper="A4")
        self.check(xq["quote"]["sides"] == 15, "copy job configured & priced", f"15 sides = PKR {xq['total_minor'] / 100:.0f}")
        s.pay(pc2, x, "COUNTER")
        wrong = s.http.post(f"/staff/jobs/{x['id']}/actions/confirm-cash", headers=ops, json={"amount_minor": xq["total_minor"] - 100})
        self.check(wrong.status_code == 409, "cashier typed the wrong amount → payment held, nothing authorised")
        s.http.post(f"/staff/jobs/{x['id']}/actions/refund-request", headers=ops, json={"note": "demo: reset held payment"})
        x2 = s.http.post("/jobs/xerox", headers=pc2).json()
        s.configure(pc2, x2, originals=5, copies=3, color="BW", duplex=False, paper="A4")
        s.pay(pc2, x2, "COUNTER")
        ok = s.http.post(f"/staff/jobs/{x2['id']}/actions/confirm-cash", headers=ops, json={"amount_minor": xq["total_minor"]})
        self.check(ok.json()["state"] == "AWAITING_STAFF", "cash confirmed by a named staff member → waiting for the copier")
        fin = s.http.post(f"/staff/jobs/{x2['id']}/actions/complete", headers=ops, json={"note": "copied on MFP-1"})
        self.check(fin.json()["state"] == "COMPLETED", "staff placed the document and completed the job")

        self.section("4  Print queue, failures and recovery (slide 3, 6)")
        s.ctx.printer.fail_next = PrintError("paper tray empty", retryable=True)
        j4 = s.upload(pc1, sample_pdf(2), "notes.pdf")
        q4 = s.configure(pc1, j4, copies=1)
        s.pay(pc1, j4, "ONLINE")
        s.webhook(s.job(pc1, j4)["payment"]["reference"], q4["total_minor"])
        d4 = s.wait_for(pc1, j4, {"COMPLETED"})
        self.check(d4["attempts"] == 2, "printer hiccup → automatic retry succeeded", "attempt 2")
        s.ctx.printer.fail_next = PrintError("connection lost after sending", retryable=False)
        j5 = s.upload(pc1, sample_pdf(2), "contract.pdf")
        q5 = s.configure(pc1, j5, copies=1)
        s.pay(pc1, j5, "ONLINE")
        s.webhook(s.job(pc1, j5)["payment"]["reference"], q5["total_minor"])
        u5 = s.wait_for(pc1, j5, {"UNCERTAIN"})
        self.check(u5["state"] == "UNCERTAIN", "unknown outcome → NOT auto-reprinted (no double print), staff is asked")
        r5 = s.http.post(f"/staff/jobs/{j5['id']}/actions/retry", headers=ops, json={"note": "no paper in tray - reprint"})
        self.check(r5.json()["state"] == "QUEUED", "staff re-authorised it (new single-use authorisation, named in the log)")
        s.wait_for(pc1, j5, {"COMPLETED"})
        # crash mid-print: stop the worker, claim a job by hand, let the lease run out, run the janitor
        s.worker.stop()
        j6 = s.upload(pc1, sample_pdf(2), "crash.pdf")
        q6 = s.configure(pc1, j6, copies=1)
        s.pay(pc1, j6, "ONLINE")
        s.webhook(s.job(pc1, j6)["payment"]["reference"], q6["total_minor"])
        with s.ctx.db.session() as db:
            claimed = job_service.claim_next(db, s.ctx)
            assert claimed, "job should be claimable"
            claimed[0].lease_expires_at = claimed[0].lease_expires_at.replace(year=2001)
        s.worker.run_janitor()
        self.check(s.job(pc1, j6)["state"] == "UNCERTAIN", "server crashed mid-print → recovery marks UNCERTAIN, never loses it")

        self.section("5  Audit: every important event, tamper-evident (slide 6)")
        v = s.http.get("/staff/audit/verify", headers=admin).json()
        self.check(v["ok"], "hash chain verifies", f"{v['checked']} events")
        with s.ctx.db.session() as db:
            db.execute(text("UPDATE audit_events SET actor='someone-else' WHERE seq = (SELECT min(seq) + 5 FROM audit_events)"))
        v2 = s.http.get("/staff/audit/verify", headers=admin).json()
        self.check(not v2["ok"], "editing one past row is detected", f"first bad entry: seq {v2.get('broken_at')}")

        stats = s.http.get("/staff/stats", headers=ops).json()
        print(f"\n{B}{G}All {self.checks} checks passed.{X}  Today: PKR {stats['revenue_minor'] / 100:.0f} revenue.")


def run_tour(keep: bool = False, database_url: Optional[str] = None) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smartprint-demo-"))
    shop = Shop(tmp, free_port(), database_url)
    try:
        shop.ctx.db.create_all()
        shop.start()
        create_admin(shop.ctx, "admin", "admin12345", "ADMIN")
        create_admin(shop.ctx, "operator", "operator12345", "OPERATOR")
        pcs = {f"PC-0{i}": {"X-PC-Key": register_pc(shop.ctx, f"PC-0{i}")} for i in (1, 2, 3, 4)}
        print(f"{B}SmartPrint demo{X}  {D}server on {shop.base}, data in {tmp}{X}")
        Tour(shop).run(pcs, shop.staff("operator", "operator12345"), shop.staff("admin", "admin12345"))
        return 0
    except AssertionError as e:
        print(f"\n{R}FAILED:{X} {e}")
        return 1
    finally:
        shop.stop()
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def run_up(port: int) -> int:
    """Long-running demo shop with sample jobs so the staff console is not empty."""
    from .server.config import get_settings
    s = get_settings()
    ctx = build_context(s)
    ctx.db.create_all()
    creds = seed_demo(ctx, s.data_dir)
    print(f"{B}Demo shop ready.{X} Logins: admin/admin12345, operator/operator12345; PC keys in {s.data_dir}/demo_credentials.json")
    print(f"Open the GUIs in other terminals:\n  SMARTPRINT_PC_NAME=PC-01 smartprint-kiosk\n  smartprint-staff\n")
    uvicorn.run("smartprint.server.main:app_factory", factory=True, host="0.0.0.0", port=port)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser("smartprint-demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    t = sub.add_parser("tour", help="narrated, self-checking tour (default)")
    t.add_argument("--keep", action="store_true", help="keep the temp data folder")
    u = sub.add_parser("up", help="run a demo shop server with seeded accounts")
    u.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    if args.cmd == "up":
        return run_up(args.port)
    return run_tour(keep=getattr(args, "keep", False))


if __name__ == "__main__":
    raise SystemExit(main())
