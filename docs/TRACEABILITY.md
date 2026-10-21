# Slide → implementation → proof

| Slide claim | Where | Proof |
|---|---|---|
| FILE→SETTINGS→PAY→PRINT (2, 11) | kiosk wizard `client/kiosk_app.py`, `views/kiosk_pages.py` | `test_client_gui.py::test_customer_journey…`, demo §1 |
| Digital file handling (3) | `services/files.py` | `test_flow.py`, file tests |
| Print configuration (3) | `PrintSettings`, page ranges | `test_pricing.py` |
| Server calculates price (4) | `services/pricing.py` | `test_client_cannot_influence_price`, demo §1 |
| Verify payment before production (4, 10) | `services/payments.py`, `jobs.authorise/claim_next` | `test_payments_security.py`, demo §2 |
| Queue & execute, job tracking (4, 6) | `worker.py`, `jobs.claim_next/finish_print` | `test_flow.py`, `test_concurrency.py` |
| Multiple PCs, one server (7) | `Workstation` + `X-PC-Key`; PC-01…04 seeded | `test_payments_security.py` (wrong PC/token) |
| FastAPI / PostgreSQL / SQLAlchemy+Alembic (7, 8) | `server/`, `alembic/` | full suite on PostgreSQL 16; `test_migrations.py` |
| PySide6 / PyInstaller (8) | `client/`, `packaging/build.py` | frozen server+kiosk built and smoke-run |
| pytest (8) | `tests/` (88) | CI |
| Adapters: printer / payment / xerox (7) | `server/adapters/` | `test_adapters.py` |
| Staff control (6) | `routers/staff.py`, `routers/admin.py`, staff console | demo §3, GUI test |
| Xerox staff-assisted (6, 10) | `AWAITING_STAFF` flow, `ManualXerox` | demo §3 |
| Audit & recovery (6) | `services/audit.py`, `jobs.recover` | `test_recovery_audit.py`, demo §4–5 |
| No customer login (10) | no account model; per-job token | API has no customer auth routes |
| Modular monolith first (8) | single deployable, module boundaries | — |
