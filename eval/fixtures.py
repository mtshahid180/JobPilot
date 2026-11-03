"""Evaluation-only profile fixture.

The product itself starts with a blank profile. The benchmark persona lives
here so relevance labels remain stable without shipping sample data to users.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobpilot.core.models import Profile

PROFILE_PATH = Path(__file__).resolve().parent / "dataset" / "profile.json"


def load_eval_profile() -> Profile:
    """Load the persona the graded job labels were written against."""
    return Profile.model_validate(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
