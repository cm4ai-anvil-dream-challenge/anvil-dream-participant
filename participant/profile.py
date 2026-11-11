"""Default participant submission profile."""

from __future__ import annotations

from pathlib import Path

from .challenge import ChallengeProfile
from .config import PARTICIPANT_EVALUATION_ID, PARTICIPANT_QUEUE_NAME


def _validate_workflow(path: Path) -> None:
    from .workflows.wdl import parse_participant_workflow

    parse_participant_workflow(path, require_exact_participant_shape=True)


PROFILE = ChallengeProfile(
    name="HPA",
    evaluation_id=PARTICIPANT_EVALUATION_ID,
    queue_name=PARTICIPANT_QUEUE_NAME,
    validate_workflow=_validate_workflow,
)
