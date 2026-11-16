"""Paid-tier plan generation (Personalized and Advanced).

No database code here except the small callbacks main.py hands in. Model
names, API keys and provider details come ONLY from the environment and never
leave the server. Errors raised from here carry a short safe `code` - users
never see provider messages.

Money safety built in:
  * every request is recorded (tokens + cost) right after it returns, before
    the JSON is checked, via the `record` callback;
  * the OpenAI client's own hidden retries are switched OFF (max_retries=0),
    so every billable request is one we know about and recorded;
  * there is a hard cap on attempts, on output tokens, on spend per
    generation and on spend per day;
  * after an attempt whose cost is unknown we do NOT try again automatically.
"""
import json
from decimal import Decimal

import config
import costing
from ai import exercise_keys, image_keys, main_prompt

MODEL_ENV = config.MODEL_ENV


class PlanGenerationError(Exception):
    """code is one of: config, timeout, rate_limit, invalid_output, provider_error, budget."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


def timeout_seconds():
    return config.int_env("PAID_AI_TIMEOUT_SECONDS", 90)


PERSONALIZED_RULES = """
PERSONALIZED PLAN RULES (this is week {week} for this user):
- You receive LAST_WEEK: what the user was given last week and how many workout days they finished.
- If completion_rate < 0.6: lower the volume about 15% (fewer exercises, shorter sessions) so the week is achievable.
- If completion_rate >= 0.85: make a small, safe step up (a few more reps, or 5-10 seconds less rest).
- Otherwise keep the difficulty similar and change some exercises so it does not feel repeated.
- USER_NOTES (if present) are the user's own words about injuries or preferences. Never include an exercise that conflicts with them.
- Respect the user's equipment and workout days exactly.
- This is general fitness guidance, not medical advice. Keep every exercise within the user's level.
OUTPUT: return ONLY the JSON workout object described above (seven day keys). No extra keys.
"""

ADVANCED_RULES = """
ADVANCED PLAN RULES (this is week {week} for this user, phase: {phase}):
- You receive HISTORY: up to the last 4 weeks (newest first) with completion_rate per week, and the newest week in full detail.
- Plan like a coach using progressive overload across weeks. Phase "deload" means about 60-70% of normal volume and easier effort. Phase "build" means a small step up from the newest week.
- Rotate accessory exercises versus the last two weeks, but keep the main movement patterns so progress is measurable.
- Balance muscle groups across the week; place light/recovery days sensibly between hard days.
- Warm-up and cool-down/stretching must match the muscles trained that day.
- Adjust sets, reps, hold times and rest per exercise to the user's level, goal and equipment. Trend down if completion_rate has been low, up if it has been high.
- USER_NOTES (if present) are the user's injuries/preferences. Strictly avoid any exercise that conflicts with them.
- This is general fitness guidance, not medical advice.
OUTPUT OVERRIDE: return ONE JSON object with exactly two keys:
  "plan": the seven-day workout object described above (day_1 ... day_7, nothing else inside it),
  "coach_notes": 2-3 short plain-English sentences telling the user what changed this week and why.
"""


def _rules_for(tier, week, phase):
    template = PERSONALIZED_RULES if tier == "personalized" else ADVANCED_RULES
    return template.format(week=week, phase=phase)


def _client_and_model(tier):
    import os
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    model = config.plan_model(tier)
    if not api_key or not model:
        raise PlanGenerationError("config", f"missing key or model for {tier}")
    if costing.pricing_for(model) is None:
        # Still allowed (you may have set a new model), but its cost will be recorded as UNKNOWN.
        print("AI PRICING WARNING: no price configured for the", tier, "model; cost will be recorded as unknown")
    from openai import OpenAI  # imported here so the free tier never needs it

    # max_retries=0: the SDK must never silently re-send (and re-bill) a request.
    return OpenAI(api_key=api_key, timeout=timeout_seconds(), max_retries=0), model


def _classify(exc):
    name = type(exc).__name__
    if "Timeout" in name:
        return "timeout"
    if "RateLimit" in name:
        return "rate_limit"
    return "provider_error"


def _split_plan_and_notes(parsed):
    """Advanced replies wrap the plan; Personalized replies are the plan."""
    if isinstance(parsed, dict) and isinstance(parsed.get("plan"), dict):
        notes = parsed.get("coach_notes")
        notes = notes.strip()[:600] if isinstance(notes, str) else None
        return parsed["plan"], notes
    return parsed, None


def _usage_numbers(response):
    """(input, output, cached_input, reasoning) from a response; None where absent."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None, None
    tokens_in = getattr(usage, "input_tokens", None)
    tokens_out = getattr(usage, "output_tokens", None)
    in_details = getattr(usage, "input_tokens_details", None)
    out_details = getattr(usage, "output_tokens_details", None)
    cached = getattr(in_details, "cached_tokens", None) if in_details is not None else None
    reasoning = getattr(out_details, "reasoning_tokens", None) if out_details is not None else None
    return tokens_in, tokens_out, cached, reasoning


def generate_paid_plan(tier, profile, history, week_number, notes, validator, record, mark=None, guard=None):
    """Returns dict(plan_json, coach_notes, model).

    validator(json_text) returns the cleaned JSON text or raises ValueError.
    record(**fields) saves one attempt (see usage.record_attempt) and returns a
        handle - called for EVERY request, including failed ones.
    mark(handle, status, error_type) changes an already-saved attempt's status.
    guard() may raise PlanGenerationError('budget') before a request is sent.
    """
    client, model = _client_and_model(tier)
    phase = "deload" if (tier == "advanced" and week_number % 4 == 0) else "build"
    instructions = main_prompt + "\n" + _rules_for(tier, week_number, phase)
    payload = {
        "USER_PROFILE": profile,
        "USER_NOTES": notes or "",
        "HISTORY" if tier == "advanced" else "LAST_WEEK": history,
        "AVAILABLE_EXERCISE_KEYS": exercise_keys,
        "AVAILABLE_IMAGE_KEYS": image_keys,
    }
    user_input = json.dumps(payload, separators=(",", ":"))

    max_attempts = config.max_ai_attempts()
    per_generation_limit = config.per_generation_limit_usd()
    spent = Decimal(0)
    any_unknown = False
    last_problem = ""
    last_code = "invalid_output"

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            # Automatic repair attempts only while spending is known and under the limit.
            if any_unknown:
                break
            if per_generation_limit is not None and spent >= per_generation_limit:
                last_code = "budget"
                break
        if guard:
            guard()
        text_in = user_input
        if last_problem:
            text_in += "\nYour previous answer was rejected: " + last_problem + "\nReturn a corrected JSON object."
        kwargs = dict(
            model=model, instructions=instructions, input=text_in,
            max_output_tokens=config.max_output_tokens(),
            text={"format": {"type": "json_object"}}, store=False,
        )
        effort = (config._env("PAID_AI_REASONING_EFFORT") or "").lower()
        if effort:
            kwargs["reasoning"] = {"effort": effort}

        common = dict(model=model, tier=tier, attempt_number=attempt)
        try:
            response = client.responses.create(**kwargs)
        except Exception as exc:
            # No usage came back: the cost of this attempt is UNKNOWN (not zero).
            any_unknown = True
            code = _classify(exc)
            record(status="error", error_type=type(exc).__name__[:60],
                   request_id=getattr(exc, "request_id", None), **common)
            raise PlanGenerationError(code, type(exc).__name__)

        tokens_in, tokens_out, cached, reasoning = _usage_numbers(response)
        request_id = getattr(response, "_request_id", None) or getattr(response, "id", None)
        cost = costing.compute_cost_usd(model, tokens_in, tokens_out, cached or 0)
        if cost is None:
            any_unknown = True
        else:
            spent += cost
        # Saved BEFORE validation, so a rejected answer or a crash still counts.
        handle = record(status="success", request_id=request_id, input_tokens=tokens_in,
                        output_tokens=tokens_out, cached_input_tokens=cached,
                        reasoning_tokens=reasoning, **common)
        try:
            parsed = json.loads(response.output_text)
            plan_obj, coach_notes = _split_plan_and_notes(parsed)
            serialized = validator(json.dumps(plan_obj))
        except Exception as exc:       # bad JSON, failed validation, anything after the response arrived
            last_problem = str(exc)[:200]
            last_code = "invalid_output"
            if mark:
                mark(handle, "invalid_output", type(exc).__name__[:60])
            continue
        return {"plan_json": serialized, "coach_notes": coach_notes, "model": model}
    raise PlanGenerationError(last_code, last_problem)
