# Architecture

Modular monolith: one deployable server, clear internal modules, room to split later.

```mermaid
flowchart LR
  subgraph Shop PCs
    K[PySide6 kiosk x4]
    S[PySide6 staff console]
  end
  K & S -- HTTPS/REST --> API[FastAPI]
  subgraph Server
    API --> SVC[services: jobs · payments · pricing · files · audit]
    W[print worker + janitor] --> SVC
    SVC --> ORM[SQLAlchemy + Alembic]
  end
  ORM --> DB[(PostgreSQL)]
  SVC -.adapter.-> PR[Printer: spool · CUPS · SumatraPDF]
  SVC -.adapter.-> PAY[Payment provider]
  SVC -.adapter.-> XR[Xerox: staff-assisted / MFP]
  PAY -- signed webhook --> API
```

## Job state machine (`services/jobs.py`, `TRANSITIONS`)
```mermaid
stateDiagram-v2
  [*] --> DRAFT
  DRAFT --> CONFIGURED
  CONFIGURED --> AWAITING_PAYMENT
  AWAITING_PAYMENT --> PAID: verified
  PAID --> QUEUED: digital (authorised)
  PAID --> AWAITING_STAFF: xerox
  QUEUED --> PRINTING
  PRINTING --> COMPLETED
  PRINTING --> QUEUED: retryable error
  PRINTING --> UNCERTAIN: unknown outcome / crash
  PRINTING --> FAILED
  UNCERTAIN --> QUEUED: staff re-authorises
  UNCERTAIN --> COMPLETED: staff confirms paper
  FAILED --> QUEUED
  AWAITING_STAFF --> COMPLETED
  QUEUED --> REFUND_PENDING
  REFUND_PENDING --> REFUNDED
  DRAFT --> EXPIRED
  CONFIGURED --> CANCELLED
```

## Key decisions
* **Server owns money.** Clients never send prices; settings freeze once payment is requested; money is integer minor units.
* **Verify, then authorise.** Payment verified → HMAC-signed single-use print authorisation bound to file hash + settings + amount. The worker refuses tampered files or replayed authorisations.
* **Never guess about paper.** If the printer outcome is unknown (timeout, crash, lease expiry) the job is `UNCERTAIN` and waits for a named human — no silent reprint, no silent loss.
* **Idempotent everywhere.** Webhook event ids are processed once; payment requests honour `Idempotency-Key`; optimistic locking (`version_id`) + `FOR UPDATE SKIP LOCKED` prevent double claims.
* **Tamper-evident audit.** Each event hashes the previous one; `GET /staff/audit/verify` pinpoints the first edited row.
* **Privacy.** No customer accounts; job access needs a secret per-job token (stored hashed); wrong token and missing job look identical.
* **Adapters** keep hardware/provider specifics out of business rules (Protocols in `server/adapters`).
