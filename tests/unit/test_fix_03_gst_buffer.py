"""Fix 03 — GST buffer on the virtual card limit.

Pure function, no external dependencies. Verifies the ×1.28 GST buffer and the
×1.05 non-GST buffer, plus rounding.
"""
from core.budget_engine import GST_BUFFER_MULTIPLIER, calculate_card_limit


def test_multiplier_is_max_gst_slab():
    assert GST_BUFFER_MULTIPLIER == 1.28  # highest Indian GST slab (28%)


def test_gst_required_applies_28pct_buffer():
    assert calculate_card_limit(10000, gst_required=True) == 12800.0


def test_no_gst_applies_5pct_buffer():
    assert calculate_card_limit(10000, gst_required=False) == 10500.0


def test_rounds_to_two_decimals():
    # 16500 * 1.28 = 21120.0 exactly
    assert calculate_card_limit(16500, gst_required=True) == 21120.0
    # 99.99 * 1.28 = 127.9872 -> rounded to 127.99
    assert calculate_card_limit(99.99, gst_required=True) == 127.99


def test_buffer_covers_full_gst_on_top_of_quote():
    # A vendor quoting 10000 then invoicing +18% GST = 11800 must still authorize.
    quote = 10000
    invoice_with_18pct = quote * 1.18
    assert calculate_card_limit(quote, gst_required=True) >= invoice_with_18pct
