"""Plan cycles: what makes a new plan REPLACE the current one instead of becoming "next week".

A cycle is a run of weekly plans built for one version of the user's fitness profile
(week 1, week 2, ...). Every plan remembers a fingerprint of the profile it was built
from. When the user edits their profile (weight, goal, experience, workout days,
equipment, date of birth, height, gender) the fingerprint changes, so the next plan
starts a NEW cycle: week 1, no history from the old profile, and the old cycle's
plans are archived (kept, but hidden) at the moment the new plan is saved.

Saving the same values again, or editing and then changing back, doesn't count as an
edit. progression_score is ignored on purpose: the app changes it automatically.
"""
import hashlib
import json

import equipment as equipment_rules


def fingerprint(profile):
    """Stable short hash of the profile fields that shape a plan. None if there is no profile."""
    if profile is None:
        return None
    data = {
        "dob": profile.dob.isoformat() if profile.dob else None,
        "height": profile.height, "weight": profile.weight,
        "gender": profile.gender, "goal": profile.goal, "experience": profile.experience,
        "workout_days": profile.workout_days,
        "equipment": sorted(equipment_rules.parse_stored(profile.equipment)),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:32]
