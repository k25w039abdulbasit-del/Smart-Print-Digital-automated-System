from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..adapters.payment import WebhookInvalid
from ..deps import get_ctx
from ..services import payments

router = APIRouter(tags=["payments"])


@router.post("/payments/webhook/{provider}")
async def webhook(provider: str, request: Request):
    """Provider -> server. Authenticity comes from the HMAC signature, not from who is calling."""
    ctx = get_ctx(request)
    if provider != ctx.provider.name:
        raise HTTPException(404, "Unknown provider")
    body = await request.body()
    headers = dict(request.headers)

    def run() -> str:
        with ctx.db.session() as db:
            return payments.handle_webhook(db, ctx, headers, body)
    from starlette.concurrency import run_in_threadpool
    try:
        return {"outcome": await run_in_threadpool(run)}
    except WebhookInvalid as e:
        raise HTTPException(400, f"Invalid webhook: {e}")
