"""Payment provider adapter (slide 7: PAYMENT - provider adapter).

Real wallets/banks (Easypaisa, JazzCash, Stripe, ...) each become one class that
implements `PaymentProvider`. The shipped `MockProvider` speaks the same signed-webhook
protocol a real provider would, so the whole verification path is exercised end to end.
"""
from __future__ import annotations

import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Protocol

from ..security import hmac_hex


class WebhookInvalid(Exception):
    pass


@dataclass
class Charge:
    txn_id: str
    payload: str              # what the kiosk shows: QR text / deep-link / instructions


@dataclass
class ProviderEvent:
    event_id: str
    reference: str
    status: str               # "succeeded" | "failed"
    amount_minor: int
    currency: str
    txn_id: str


class PaymentProvider(Protocol):
    name: str

    def create_charge(self, reference: str, amount_minor: int, currency: str, ttl_s: int) -> Charge: ...
    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent: ...
    def query(self, reference: str) -> Optional[ProviderEvent]: ...
    def refund(self, txn_id: str, amount_minor: int) -> bool: ...


SIG_HEADER = "x-smartprint-signature"
TS_HEADER = "x-smartprint-timestamp"
TOLERANCE_S = 300


def sign_webhook(secret: str, body: bytes, ts: Optional[int] = None) -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    return {TS_HEADER: str(ts), SIG_HEADER: hmac_hex(secret, f"{ts}.{body.decode()}")}


class MockProvider:
    """Stand-in wallet. `simulate_payment()` produces a correctly signed webhook body."""
    name = "mock"

    def __init__(self, webhook_secret: str):
        self.secret = webhook_secret
        self._paid: dict[str, ProviderEvent] = {}
        self._refunded: set[str] = set()

    def create_charge(self, reference: str, amount_minor: int, currency: str, ttl_s: int) -> Charge:
        txn = f"mock_{uuid.uuid4().hex[:12]}"
        return Charge(txn, f"smartprint-pay://{reference}?amount={amount_minor}&cur={currency}")

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent:
        h = {k.lower(): v for k, v in headers.items()}
        try:
            ts, sig = int(h[TS_HEADER]), h[SIG_HEADER]
        except (KeyError, ValueError) as e:
            raise WebhookInvalid("missing signature headers") from e
        if abs(time.time() - ts) > TOLERANCE_S:
            raise WebhookInvalid("timestamp outside tolerance (replay?)")
        good = hmac_hex(self.secret, f"{ts}.{body.decode()}")
        if not hmac.compare_digest(sig, good):
            raise WebhookInvalid("bad signature")
        try:
            d = json.loads(body)
            ev = ProviderEvent(d["event_id"], d["reference"], d["status"], int(d["amount_minor"]),
                               d["currency"], d["txn_id"])
        except (KeyError, ValueError, TypeError) as e:
            raise WebhookInvalid("malformed body") from e
        if ev.status == "succeeded":
            self._paid[ev.reference] = ev
        return ev

    def query(self, reference: str) -> Optional[ProviderEvent]:
        return self._paid.get(reference)

    def refund(self, txn_id: str, amount_minor: int) -> bool:
        self._refunded.add(txn_id)
        return True

    # --- helpers used by the dev endpoint / tests / demo ---
    def simulate_payment(self, reference: str, amount_minor: int, currency: str, txn_id: str,
                         event_id: Optional[str] = None, status: str = "succeeded") -> tuple[bytes, dict[str, str]]:
        body = json.dumps({"event_id": event_id or f"evt_{uuid.uuid4().hex[:12]}", "reference": reference,
                           "status": status, "amount_minor": amount_minor, "currency": currency,
                           "txn_id": txn_id}).encode()
        return body, sign_webhook(self.secret, body)
