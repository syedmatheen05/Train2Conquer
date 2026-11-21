"""Equipment choices for the fitness profile, and how they limit AI workouts.

One place decides everything about equipment, so the form, the database, the
AI prompt and the plan checker can never disagree:

  * "No equipment" (none) -> bodyweight exercises only.
  * "Gym"                 -> a standard gym: every exercise in our catalogue.
  * Individual items      -> bodyweight exercises PLUS exercises that need
                             only the items the user ticked.

"No equipment" and "Gym" are mutually exclusive with each other and with every
individual item. Individual items can be combined freely.

How a profile is stored: the same comma-separated text column as before
(e.g. "dumbbells,bench", "none", "gym"). No database change is needed, and
profiles saved before "Gym" existed keep working unchanged. A legacy blank
value is read as "none".

What the AI may use is enforced twice, not just asked for in the prompt:
  1. the list of exercise keys sent to the AI is filtered (allowed_exercise_keys);
  2. every generated plan is checked on the server (disallowed_exercises).
"""

NONE = "none"
GYM = "gym"

# (value, label) - the order shown on the form.
CHOICES = [
    (NONE, "No equipment"),
    (GYM, "Gym"),
    ("dumbbells", "Dumbbells"),
    ("bands", "Resistance bands"),
    ("pullup", "Pull-up bar"),
    ("bench", "Bench"),
    ("kettlebell", "Kettlebell"),
    ("barbell", "Barbell"),
]
LABELS = dict(CHOICES)
EXCLUSIVE = (NONE, GYM)                                   # each excludes every other choice
INDIVIDUAL = tuple(v for v, _ in CHOICES if v not in EXCLUSIVE)

# Equipment an exercise REQUIRES. Anything not listed is a bodyweight exercise.
# "rope" is a jump rope: it is not a profile choice, so only a Gym profile has it.
# Household items (a chair for incline/decline push-ups, a backpack for
# "lunges with backpack") are deliberately NOT treated as equipment - those
# exercises exist in the catalogue for people with no equipment.
GYM_ONLY_TAGS = ("rope",)
EXERCISE_EQUIPMENT = {
    "pull-ups": "pullup",
    "skipping": "rope",
    "dumbbell-front-raises": "dumbbells",
    "dumbbell-rear-delt-fly": "dumbbells",
    "dumbbell-lateral-raises": "dumbbells",
    "dumbell-bicep-curls": "dumbbells",
    "tricep-kickbacks": "dumbbells",
    "tricep-overhead-single-arm-dumbell-extension-left": "dumbbells",
    "tricep-overhead-single-arm-dumbell-extension-right": "dumbbells",
    "lunges-with-dumbells": "dumbbells",
    "barbell-back-squat": "barbell",
    "barbell-bent-over-row": "barbell",
    "barbell-overhead-press": "barbell",
    "barbell-skull-crushers": "barbell",
}


def parse_stored(text):
    """The saved text -> a clean list. Unknown words are dropped; blank -> ['none']."""
    return normalise(str(text or "").split(","))


def serialise(values):
    return ",".join(normalise(values))


def normalise(values):
    """Known values only, no repeats, in form order. Empty -> ['none'] (the legacy meaning)."""
    wanted = {str(v).strip().lower() for v in (values or [])}
    clean = [value for value, _ in CHOICES if value in wanted]
    return clean or [NONE]


def mode(values):
    """'none', 'gym' or 'custom' for a saved/selected list."""
    clean = normalise(values)
    if clean == [NONE]:
        return NONE
    if clean == [GYM]:
        return GYM
    return "custom"


def validate_selection(values):
    """Returns a user-safe error message for a bad combination, or None if it is fine."""
    chosen = {str(v).strip().lower() for v in (values or [])}
    if not chosen:
        return "Select at least one option - choose \"No equipment\" if you don't have any."
    if len(chosen) > 1 and chosen & set(EXCLUSIVE):
        return "\"No equipment\" and \"Gym\" can't be combined with other equipment options."
    return None


def available_tags(values):
    """The equipment tags an exercise may require for this profile."""
    kind = mode(values)
    if kind == NONE:
        return set()
    if kind == GYM:
        return set(INDIVIDUAL) | set(GYM_ONLY_TAGS)
    return set(normalise(values))


def exercise_allowed(exercise_key, values):
    needs = EXERCISE_EQUIPMENT.get(exercise_key)
    return needs is None or needs in available_tags(values)


def allowed_exercise_keys(all_keys, values):
    """The catalogue filtered to what this profile can actually do (order kept)."""
    return [key for key in all_keys if exercise_allowed(key, values)]


def disallowed_exercises(plan, values):
    """Exercise keys used in a parsed plan that this profile has no equipment for."""
    bad = []
    for day in (plan or {}).values():
        for item in (day[1:] if isinstance(day, list) else []):
            key = item.get("exercise") if isinstance(item, dict) else None
            if key and not exercise_allowed(key, values) and key not in bad:
                bad.append(key)
    return bad


def prompt_rules(values):
    """The EQUIPMENT block added to the AI prompt (wording depends on the profile)."""
    kind = mode(values)
    header = "=================================================\nEQUIPMENT AVAILABLE TO THIS USER (HARD RULE)\n=================================================\n"
    if kind == NONE:
        body = ("The user has NO equipment. Use bodyweight exercises ONLY.\n"
                "Do NOT use any exercise that needs dumbbells, a barbell, a pull-up bar, a bench, "
                "kettlebells, bands, a jump rope or any machine.")
    elif kind == GYM:
        body = ("The user trains at a STANDARD GYM with common equipment (dumbbells, barbells, a bench, "
                "a pull-up bar, kettlebells, bands, a jump rope and the usual machines).\n"
                "Choose freely from the AVAILABLE EXERCISE KEYS. Do not assume anything exotic.")
    else:
        names = ", ".join(LABELS[v].lower() for v in normalise(values))
        body = ("The user owns ONLY this equipment: " + names + ".\n"
                "Use bodyweight exercises plus exercises that need only the equipment listed above.\n"
                "Do NOT use any exercise that needs equipment that is not listed.")
    return (header + body + "\nNever invent equipment, and only use exercise keys from AVAILABLE EXERCISE KEYS "
            "(the list already excludes everything this user cannot do).\n")
