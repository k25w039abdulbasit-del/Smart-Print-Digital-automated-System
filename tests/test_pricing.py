import copy

import pytest

from smartprint.server.services import pricing
from smartprint.server.services.pricing import PrintSettings, PricingError, compute_quote, parse_page_range
from smartprint.shared.enums import ColorMode, JobKind, PaperSize

CFG = pricing.DEFAULT_PRICE_CONFIG


def q(pages=3, kind=JobKind.DIGITAL, **kw):
    return compute_quote(CFG, kind, PrintSettings(**kw), pages)


def test_simple_bw():
    r = q(3)
    assert (r.sides, r.total_minor) == (3, 3000)


def test_copies_multiply_sides():
    assert q(3, copies=4).total_minor == 12000


def test_color_and_paper():
    assert q(2, color=ColorMode.COLOR).total_minor == 6000
    assert q(2, paper=PaperSize.A3).total_minor == 5000


def test_duplex_discount_and_sheets():
    r = q(4, duplex=True)
    assert r.total_minor == 4 * 900 == 3600 and r.sheets == 2


def test_page_range_limits_billing():
    assert q(10, page_range="1-2,5").sides == 3


def test_volume_tier_and_rounding():
    r = q(100)                       # 100 sides -> 5 % off
    assert r.discount_minor == 5000 and r.total_minor == 95000
    assert q(500).total_minor == 500 * 1000 * 90 // 100


def test_minimum_charge():
    r = q(kind=JobKind.XEROX, originals=1)
    assert r.total_minor == 1000


def test_xerox_requires_originals():
    with pytest.raises(PricingError):
        q(kind=JobKind.XEROX)


def test_totals_are_always_whole_rupees_and_lines_sum_up():
    for pages in (1, 2, 7, 33, 120):
        for dup in (False, True):
            r = q(pages, duplex=dup, copies=3)
            assert r.total_minor % 100 == 0
            assert sum(l.amount_minor for l in r.lines) == r.total_minor


@pytest.mark.parametrize("spec,n,expect", [
    ("", 3, [1, 2, 3]), ("2", 5, [2]), ("1-3,2-4", 5, [1, 2, 3, 4]), ("5,1", 5, [5, 1]),
])
def test_parse_page_range(spec, n, expect):
    assert parse_page_range(spec, n) == expect


@pytest.mark.parametrize("spec", ["0", "3-1", "9", "1-9"])
def test_parse_page_range_rejects(spec):
    with pytest.raises(PricingError):
        parse_page_range(spec, 5)


@pytest.mark.parametrize("bad", ["a", "1-", "1,,2", "-3"])
def test_settings_reject_garbage_range(bad):
    with pytest.raises(ValueError):
        PrintSettings(page_range=bad)


def test_price_config_validation():
    pricing.validate_price_config(CFG)
    bad = copy.deepcopy(CFG)
    bad["services"]["digital"]["A4"]["BW"] = 0
    with pytest.raises(PricingError):
        pricing.validate_price_config(bad)
    with pytest.raises(PricingError):
        pricing.validate_price_config({"currency": "PKR"})
