"""Thin, synchronous HTTP client for the SmartPrint server (used from worker threads)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx

from .settings import ClientSettings


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _raise(r: httpx.Response) -> Any:
    if r.status_code >= 400:
        try:
            d = r.json().get("detail", r.text)
        except Exception:  # noqa: BLE001
            d = r.text
        if isinstance(d, list):      # pydantic validation errors
            d = "; ".join(str(x.get("msg", x)) for x in d)
        raise ApiError(r.status_code, str(d))
    if r.headers.get("content-type", "").startswith("image/"):
        return r.content
    return r.json() if r.content else None


class KioskApi:
    """Customer-facing calls. A job's secret token never leaves this object."""

    def __init__(self, cfg: ClientSettings):
        self.cfg = cfg
        self.http = httpx.Client(base_url=cfg.api, timeout=20, headers={"X-PC-Key": cfg.pc_key})
        self._tokens: dict[str, str] = {}

    def _jh(self, job_id: str) -> dict[str, str]:
        return {"X-Job-Token": self._tokens[job_id]}

    def _remember(self, job: dict) -> dict:
        if job.get("access_token"):
            self._tokens[job["id"]] = job["access_token"]
        return job

    def health(self) -> dict:
        return httpx.get(self.cfg.server.rstrip("/") + "/health", timeout=5).json()

    def rate_card(self) -> dict:
        return _raise(self.http.get("/pricing"))

    def upload(self, path: Path) -> dict:
        with open(path, "rb") as f:
            return self._remember(_raise(self.http.post("/jobs/digital", files={"file": (path.name, f)})))

    def new_xerox(self) -> dict:
        return self._remember(_raise(self.http.post("/jobs/xerox")))

    def job(self, job_id: str) -> dict:
        return _raise(self.http.get(f"/jobs/{job_id}", headers=self._jh(job_id)))

    def set_settings(self, job_id: str, settings: dict) -> dict:
        return _raise(self.http.put(f"/jobs/{job_id}/settings", json=settings, headers=self._jh(job_id)))

    def pay(self, job_id: str, method: str, idem: str) -> dict:
        return _raise(self.http.post(f"/jobs/{job_id}/pay", json={"method": method},
                                     headers={**self._jh(job_id), "Idempotency-Key": idem}))

    def cancel(self, job_id: str) -> dict:
        return _raise(self.http.post(f"/jobs/{job_id}/cancel", headers=self._jh(job_id)))

    def withdraw_payment(self, job_id: str) -> dict:
        return _raise(self.http.post(f"/jobs/{job_id}/payment/withdraw", headers=self._jh(job_id)))

    def preview(self, job_id: str, page: int = 1, width: int = 520) -> bytes:
        return _raise(self.http.get(f"/jobs/{job_id}/preview", params={"page": page, "width": width},
                                    headers=self._jh(job_id)))

    def simulate_payment(self, reference: str) -> dict:
        return _raise(self.http.post(f"/dev/payments/{reference}/simulate"))


class StaffApi:
    def __init__(self, cfg: ClientSettings):
        self.cfg = cfg
        self.http = httpx.Client(base_url=cfg.api, timeout=20)
        self.username = ""
        self.role = ""

    def login(self, username: str, password: str) -> None:
        d = _raise(self.http.post("/staff/login", json={"username": username, "password": password}))
        self.http.headers["Authorization"] = f"Bearer {d['token']}"
        self.username, self.role = d["username"], d["role"]

    def _get(self, path: str, **params: Any) -> Any:
        return _raise(self.http.get(path, params={k: v for k, v in params.items() if v not in (None, "")}))

    def _post(self, path: str, body: Optional[dict] = None) -> Any:
        return _raise(self.http.post(path, json=body or {}))

    def jobs(self, **f: Any) -> list[dict]:
        return self._get("/staff/jobs", **f)

    def action(self, job_id: str, action: str, note: str = "", amount_minor: Optional[int] = None) -> dict:
        return self._post(f"/staff/jobs/{job_id}/actions/{action}", {"note": note, "amount_minor": amount_minor})

    def stats(self) -> dict:
        return self._get("/staff/stats")

    def system(self) -> dict:
        return self._get("/staff/system")

    def audit(self, limit: int = 300, before_seq: Optional[int] = None) -> list[dict]:
        return self._get("/staff/audit", limit=limit, before_seq=before_seq)

    def verify_audit(self) -> dict:
        return self._get("/staff/audit/verify")

    # admin
    def pricing(self) -> list[dict]:
        return self._get("/admin/pricing")

    def publish_pricing(self, config: dict) -> dict:
        return self._post("/admin/pricing", {"config": config})

    def workstations(self) -> list[dict]:
        return self._get("/admin/workstations")

    def register_ws(self, name: str, printer: Optional[str] = None) -> dict:
        return self._post("/admin/workstations", {"name": name, "printer_name": printer})

    def set_ws_active(self, ws_id: int, active: bool) -> dict:
        return self._post(f"/admin/workstations/{ws_id}/active/{str(active).lower()}")

    def staff_list(self) -> list[dict]:
        return self._get("/admin/staff")

    def create_staff(self, username: str, password: str, role: str) -> dict:
        return self._post("/admin/staff", {"username": username, "password": password, "role": role})
