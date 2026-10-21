# Operations

## Server (one machine)
**Docker:** create `.env` with `POSTGRES_PASSWORD`, `SMARTPRINT_SECRET_KEY`, `SMARTPRINT_WEBHOOK_SECRET`, then `docker compose up -d` (migrates on start).
**Without Docker:** install PostgreSQL, copy `.env.example` → `.env`, then
```
smartprint-server migrate
smartprint-server create-admin boss "<strong password>"
smartprint-server register-pc PC-01 --printer "Shop-Printer"     # prints the PC's API key ONCE
smartprint-server serve
```
`environment=prod` refuses to start with default secrets and disables the demo "simulate payment" endpoint.
Put TLS in front on the LAN (Caddy/nginx) so `X-PC-Key` and staff tokens are encrypted.

## Printing
`SMARTPRINT_PRINTER_BACKEND`: `sumatra` (Windows + SumatraPDF), `cups` (Linux/macOS), `spool` (demo). Print one test job and confirm duplex/colour/paper before opening the shop.

## Shop PCs
Build on Windows: `python packaging/build.py kiosk staff server` → `dist/`. On each PC create `%USERPROFILE%\.smartprint\client.json`:
`{"server": "https://shop-server", "pc_name": "PC-01", "pc_key": "spk_..."}` and launch `smartprint-kiosk --fullscreen --no-dev-tools`.

## Daily
* Staff console → **Needs attention** filter: `UNCERTAIN`, `FAILED`, `REFUND_PENDING` jobs.
* Audit tab → **Verify integrity**, or `smartprint-server verify-audit` (non-zero exit on tampering).
* Back up PostgreSQL nightly (`pg_dump`); uploaded files are deleted automatically after use.
