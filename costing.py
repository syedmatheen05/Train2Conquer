"""OpenAI cost calculation. Money is ALWAYS Decimal, never float.

PRICES: per 1,000,000 tokens, in US dollars, copied from OpenAI's published
model pages (developers.openai.com) on 13 Nov 2026. If OpenAI changes a price,
edit this table or set MODEL_PRICING_JSON in the environment - no other code
needs to change. A model that is not listed has UNKNOWN cost (None), never 0.

Reasoning tokens are billed by OpenAI as output tokens and are already inside
`output_tokens`, so they are recorded separately but never added twice.
"""
import json
import os
from decimal import Decimal, ROUND_HALF_UP

import config

PRICING_SOURCE = "https://developers.openai.com/api/docs/models (checked 2026-11-13)"

# Above this many input tokens OpenAI uses different (long-context) rates that
# are not configured here, so such a request is reported as unknown cost.
LONG_CONTEXT_INPUT_TOKENS = 272_000

MODEL_PRICING = {
    "gpt-6-luna": {
        "input": Decimal("0.10"), "cached_input": Decimal("0.01"), "output": Decimal("0.50"),
    },
    "gpt-6.1-sol": {
        "input": Decimal("2.00"), "cached_input": Decimal("0.10"), "output": Decimal("10.00"),
    },
}

_MILLION = Decimal(1_000_000)


def _pricing_table():
    """Built-in table, plus optional overrides from MODEL_PRICING_JSON, e.g.
    {"my-model": {"input": "1.0", "cached_input": "0.1", "output": "4.0"}}"""
    table = dict(MODEL_PRICING)
    raw = (os.environ.get("MODEL_PRICING_JSON") or "").strip()
    if raw:
        try:
            for model, rates in json.loads(raw).items():
                table[model] = {k: Decimal(str(v)) for k, v in rates.items()}
        except (ValueError, TypeError, ArithmeticError, AttributeError):
            pass   # a bad override is ignored; built-in prices still apply
    return table


def pricing_for(model):
    return _pricing_table().get(model)


def money(value, places="0.000001"):
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def compute_cost_usd(model, input_tokens, output_tokens, cached_input_tokens=0):
    """Exact USD cost for one API response, or None if it cannot be known."""
    rates = pricing_for(model)
    if rates is None or input_tokens is None or output_tokens is None:
        return None
    if input_tokens > LONG_CONTEXT_INPUT_TOKENS:
        return None
    cached = min(max(cached_input_tokens or 0, 0), input_tokens)
    fresh = input_tokens - cached
    cost = (Decimal(fresh) * rates["input"]
            + Decimal(cached) * rates.get("cached_input", rates["input"])
            + Decimal(output_tokens) * rates["output"]) / _MILLION
    return money(cost)


def usd_to_inr(cost_usd, rate=None):
    """INR value of a USD amount using the configured rate (None if unknown)."""
    if cost_usd is None:
        return None
    rate = rate if rate is not None else config.usd_to_inr()
    if rate is None:
        return None
    return money(cost_usd * rate, "0.0001")


def worst_case_usd(model, input_tokens_estimate, max_output_tokens):
    """Upper bound for one request, used to refuse calls that could exceed a limit."""
    return compute_cost_usd(model, input_tokens_estimate, max_output_tokens)
