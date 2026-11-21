"""AI cost calculation. Money is ALWAYS Decimal, never float.

All plans use Google Gemini on the FREE tier, so a generation costs nothing:
cost is recorded as exactly 0 for Gemini models. Any other/unknown model id
records the cost as None (= UNKNOWN, never silently 0), so a misconfiguration
can't hide spending.

Old usage rows written before the Gemini-only switch keep the cost that was
saved on them at the time; nothing here recalculates history.
"""
from decimal import ROUND_HALF_UP, Decimal

FREE_TIER_PREFIX = "gemini"


def money(value, places="0.000001"):
    return Decimal(value).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def is_free_tier(model):
    return str(model or "").lower().startswith(FREE_TIER_PREFIX)


def compute_cost_usd(model, input_tokens=None, output_tokens=None, cached_input_tokens=0):
    """Decimal(0) for Gemini free tier; None (unknown) for anything else."""
    return Decimal(0) if is_free_tier(model) else None


def usd_to_inr(cost_usd, rate=None):
    """Zero stays zero without needing an exchange rate; unknown stays unknown."""
    if cost_usd is None:
        return None
    if cost_usd == 0:
        return Decimal(0)
    if rate is None:
        return None
    return money(Decimal(cost_usd) * Decimal(rate), "0.0001")
