"""Plan generation for ALL three plan types, through Gemini (gemini_service.py).

  basic         free             last week's plan -> a fresh, similar week
  personalized  paid (Rs 5)      adapts to LAST_WEEK completion and the user's notes
  advanced      paid (Rs 15*)    multi-week HISTORY, progressive overload, deload weeks,
                                 plus short coach notes
  (* the real price lives in config.py / env, never here)

The three share one prompt core (ai.main_prompt + the EQUIPMENT rule) and one
Gemini helper; they differ in the extra rules and in how much history they get.
Paid plans are only generated after main.py has verified the payment - this
module never decides that.

No database code here except the small callbacks main.py hands in. Errors raised
carry a short safe `code`; users never see provider messages.
"""
import json

import config
import equipment as equipment_rules
import gemini_service
from ai import exercise_keys_for, image_keys, main_prompt


class PlanGenerationError(Exception):
    """code is one of: config, timeout, rate_limit, invalid_output, provider_error, budget."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code


BASIC_RULES = """
BASIC PLAN RULES (this is week {week} for this user):
- This is the free plan: a clear, safe, general 7-day plan built from the user's profile.
- If PREVIOUS_PLAN is present, keep the difficulty similar but change some exercises so the week does not feel repeated.
- Respect the user's equipment (see the EQUIPMENT rule) and workout days exactly.
OUTPUT: return ONLY the JSON workout object described above (seven day keys). No extra keys.
"""

PERSONALIZED_RULES = """
PERSONALIZED PLAN RULES (this is week {week} for this user):
- You receive LAST_WEEK: what the user was given last week and how many workout days they finished.
- If completion_rate < 0.6: lower the volume about 15% (fewer exercises, shorter sessions) so the week is achievable.
- If completion_rate >= 0.85: make a small, safe step up (a few more reps, or 5-10 seconds less rest).
- Otherwise keep the difficulty similar and change some exercises so it does not feel repeated.
- USER_NOTES (if present) are the user's own words about injuries or preferences. Never include an exercise that conflicts with them.
- Respect the user's equipment (see the EQUIPMENT rule) and workout days exactly.
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

_RULES = {"basic": BASIC_RULES, "personalized": PERSONALIZED_RULES, "advanced": ADVANCED_RULES}


def _rules_for(tier, week, phase):
    return _RULES[tier].format(week=week, phase=phase)


def _split_plan_and_notes(parsed):
    """Advanced replies wrap the plan; Basic and Personalized replies are the plan itself."""
    if isinstance(parsed, dict) and isinstance(parsed.get("plan"), dict):
        notes = parsed.get("coach_notes")
        notes = notes.strip()[:600] if isinstance(notes, str) else None
        return parsed["plan"], notes
    return parsed, None


def generate_plan(tier, profile, history, week_number, notes, validator, record, mark=None, guard=None):
    """Returns dict(plan_json, coach_notes, model).

    validator(json_text) returns the cleaned JSON text or raises ValueError.
    record(**fields) saves one attempt (see usage.record_attempt) - called for EVERY request, failed ones too.
    mark(handle, status, error_type) changes an already-saved attempt's status.
    guard() may raise PlanGenerationError('budget') before a request is sent.
    """
    if tier not in _RULES:
        raise PlanGenerationError("config", f"unknown plan type {tier!r}")
    phase = "deload" if (tier == "advanced" and week_number % 4 == 0) else "build"
    instructions = (main_prompt + "\n" + equipment_rules.prompt_rules(profile.get("equipment"))
                    + "\n" + _rules_for(tier, week_number, phase))
    payload = {"USER_PROFILE": profile}
    if tier != "basic":
        payload["USER_NOTES"] = notes or ""
    payload[{"basic": "PREVIOUS_PLAN", "personalized": "LAST_WEEK", "advanced": "HISTORY"}[tier]] = history
    payload["AVAILABLE_EXERCISE_KEYS"] = exercise_keys_for(profile)      # already limited to the user's equipment
    payload["AVAILABLE_IMAGE_KEYS"] = image_keys
    user_input = json.dumps(payload, separators=(",", ":"))

    last_problem, last_code = "", "invalid_output"
    for attempt in range(1, config.max_ai_attempts() + 1):
        if guard:
            guard()
        text_in = user_input
        if last_problem:
            text_in += "\nYour previous answer was rejected: " + last_problem + "\nReturn a corrected JSON object."
        common = dict(model=config.gemini_model(), tier=tier, attempt_number=attempt)
        try:
            result = gemini_service.generate_json(text_in, system_instruction=instructions, tier=tier)
        except gemini_service.GeminiError as exc:
            record(status="error", error_type=exc.code[:60], **common)
            raise PlanGenerationError(exc.code, exc.code)
        handle = record(status="success", input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                        cached_input_tokens=result.cached_tokens, reasoning_tokens=result.reasoning_tokens, **common)
        try:
            parsed = gemini_service.extract_json_object(result.text)
            plan_obj, coach_notes = _split_plan_and_notes(parsed)
            serialized = validator(json.dumps(plan_obj))
        except Exception as exc:        # malformed JSON, failed validation, incomplete plan...
            last_problem = str(exc)[:200]
            last_code = "invalid_output"
            if mark:
                mark(handle, "invalid_output", type(exc).__name__[:60])
            continue
        return {"plan_json": serialized, "coach_notes": coach_notes, "model": result.model}
    raise PlanGenerationError(last_code, last_problem)
