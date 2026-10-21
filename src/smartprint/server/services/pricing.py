"""Server-side pricing engine - pure functions, no I/O, exhaustively unit-tested.

The client never sends a price. It sends *settings*; the server turns them into a
Quote using the active, versioned PriceList. All arithmetic is integer minor units
via Decimal with explicit rounding, so there is no floating point money anywhere.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from ...shared.enums import ColorMode, JobKind, PaperSize

MAX_COPIES = 500

DEFAULT_PRICE_CONFIG: dict[str, Any] = {
    "currency": "PKR",
    # minor units (1/100 rupee) charged per printed side
    "services": {
        "digital": {
            "A4": {"BW": 1000, "COLOR": 3000},
            "LETTER": {"BW": 1000, "COLOR": 3000},
            "A3": {"BW": 2500, "COLOR": 7000},
        },
        "xerox": {
            "A4": {"BW": 500, "COLOR": 2500},
            "LETTER": {"BW": 500, "COLOR": 2500},
            "A3": {"BW": 1500, "COLOR": 6000},
        },
    },
    "duplex_discount_pct": 10,           # off the per-side price when printing on both sides
    "volume_tiers": [                    # on total printed sides
        {"min_sides": 100, "discount_pct": 5},
        {"min_sides": 500, "discount_pct": 10},
        {"min_sides": 2000, "discount_pct": 15},
    ],
    "service_fee_minor": {"digital": 0, "xerox": 0},
    "minimum_charge_minor": 1000,
    "round_to_minor": 100,            # charge whole rupees
}


class PricingError(ValueError):
    pass


class PrintSettings(BaseModel):
    copies: int = Field(1, ge=1, le=MAX_COPIES)
    color: ColorMode = ColorMode.BW
    duplex: bool = False
    paper: PaperSize = PaperSize.A4
    page_range: str = ""                     # "" = all pages, else "1-3,5,8-10"
    originals: Optional[int] = Field(None, ge=1, le=2000)   # xerox: number of physical pages

    @field_validator("page_range")
    @classmethod
    def _clean_range(cls, v: str) -> str:
        v = (v or "").replace(" ", "")
        if v and not re.fullmatch(r"\d+(-\d+)?(,\d+(-\d+)?)*", v):
            raise ValueError("page range must look like 1-3,5,8-10")
        return v


def parse_page_range(spec: str, page_count: int) -> list[int]:
    """'1-3,5' -> [1,2,3,5]  (1-based, ordered, de-duplicated, validated)."""
    if not spec:
        return list(range(1, page_count + 1))
    pages: list[int] = []
    seen: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if lo < 1 or hi < lo:
            raise PricingError(f"invalid range '{part}'")
        if hi > page_count:
            raise PricingError(f"page {hi} is beyond the document ({page_count} pages)")
        for p in range(lo, hi + 1):
            if p not in seen:
                seen.add(p)
                pages.append(p)
    return pages


@dataclass
class QuoteLine:
    label: str
    qty: int
    unit_minor: int
    amount_minor: int


@dataclass
class Quote:
    currency: str
    kind: str
    pages_selected: int
    copies: int
    sides: int
    sheets: int
    lines: list[QuoteLine] = field(default_factory=list)
    subtotal_minor: int = 0
    discount_minor: int = 0
    fee_minor: int = 0
    rounding_minor: int = 0
    total_minor: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _round(d: Decimal, unit: int = 1) -> int:
    """Round half-up to a multiple of `unit` minor units."""
    q = (d / unit).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(q) * unit


_COLOR_NAME = {"BW": "Black & white", "COLOR": "Colour"}


def compute_quote(cfg: dict[str, Any], kind: JobKind, settings: PrintSettings, page_count: int) -> Quote:
    svc_key = "digital" if kind == JobKind.DIGITAL else "xerox"
    try:
        unit = int(cfg["services"][svc_key][settings.paper.value][settings.color.value])
    except KeyError as e:  # pragma: no cover - guarded by price list validation
        raise PricingError(f"no price for {svc_key}/{settings.paper.value}/{settings.color.value}") from e

    if kind == JobKind.DIGITAL:
        pages = len(parse_page_range(settings.page_range, page_count))
    else:
        if not settings.originals:
            raise PricingError("xerox jobs need the number of original pages")
        pages = settings.originals

    copies = settings.copies
    sides = pages * copies
    sheets = math.ceil(pages / 2) * copies if settings.duplex else sides
    q = Quote(cfg["currency"], kind.value, pages, copies, sides, sheets)

    base_unit = Decimal(unit)
    if settings.duplex:
        pct = Decimal(cfg.get("duplex_discount_pct", 0))
        eff = (base_unit * (100 - pct) / 100)
        eff_int = _round(eff)
        q.lines.append(QuoteLine(f"{_COLOR_NAME[settings.color.value]} {settings.paper.value} sides (double-sided rate)", sides, eff_int, sides * eff_int))
    else:
        eff_int = unit
        q.lines.append(QuoteLine(f"{_COLOR_NAME[settings.color.value]} {settings.paper.value} sides", sides, eff_int, sides * eff_int))

    subtotal = sides * eff_int
    q.subtotal_minor = subtotal

    tier_pct = 0
    for t in sorted(cfg.get("volume_tiers", []), key=lambda t: t["min_sides"]):
        if sides >= t["min_sides"]:
            tier_pct = t["discount_pct"]
    if tier_pct:
        q.discount_minor = _round(Decimal(subtotal) * tier_pct / 100)
        q.lines.append(QuoteLine(f"Volume discount {tier_pct}%", 1, -q.discount_minor, -q.discount_minor))

    fee = int(cfg.get("service_fee_minor", {}).get(svc_key, 0))
    if fee:
        q.fee_minor = fee
        q.lines.append(QuoteLine("Service fee", 1, fee, fee))

    raw = subtotal - q.discount_minor + fee
    minimum = int(cfg.get("minimum_charge_minor", 0))
    if raw < minimum:
        q.lines.append(QuoteLine("Minimum charge adjustment", 1, minimum - raw, minimum - raw))
        raw = minimum

    total = _round(Decimal(raw), int(cfg.get("round_to_minor", 1)))
    q.rounding_minor = total - raw
    if q.rounding_minor:
        q.lines.append(QuoteLine("Rounding", 1, q.rounding_minor, q.rounding_minor))
    q.total_minor = total
    return q


def validate_price_config(cfg: dict[str, Any]) -> None:
    """Reject malformed price lists before they can ever price a customer."""
    try:
        if len(cfg["currency"]) != 3:
            raise PricingError("currency must be a 3-letter code")
        for svc in ("digital", "xerox"):
            for paper in PaperSize:
                for color in ColorMode:
                    v = cfg["services"][svc][paper.value][color.value]
                    if not isinstance(v, int) or v <= 0:
                        raise PricingError(f"{svc}/{paper.value}/{color.value} must be a positive integer")
        for key in ("duplex_discount_pct",):
            if not 0 <= cfg.get(key, 0) <= 90:
                raise PricingError(f"{key} must be 0-90")
        for t in cfg.get("volume_tiers", []):
            if t["min_sides"] < 1 or not 0 <= t["discount_pct"] <= 90:
                raise PricingError("invalid volume tier")
        if cfg.get("round_to_minor", 1) < 1:
            raise PricingError("round_to_minor must be >= 1")
    except (KeyError, TypeError) as e:
        raise PricingError(f"malformed price list: {e}") from e
