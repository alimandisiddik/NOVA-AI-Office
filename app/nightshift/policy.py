"""Pure policy helpers; this sprint deliberately contains no executors."""

from __future__ import annotations

from app.nightshift.classifier import APPROVED_OVERNIGHT
from app.nightshift.models import NightJobClassification


def may_run_automatically(mode: str, classification: NightJobClassification) -> bool:
    """Return whether a future executor may begin the safe draft lifecycle."""
    return mode == "night_shift" and classification.disposition == APPROVED_OVERNIGHT
