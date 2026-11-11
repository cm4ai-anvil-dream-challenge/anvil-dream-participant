"""Public challenge profile for the HPA participant workflow."""

from __future__ import annotations

from pathlib import Path

from ...challenge import ChallengeProfile


def _validate_workflow(path: Path) -> None:
    from .workflows import parse_participant_workflow

    parse_participant_workflow(path, require_exact_participant_shape=True)


PROFILE = ChallengeProfile(
    name="HPA",
    evaluation_id="9620108",
    queue_name="CM4AI-AnVIL DREAM Challenge Submissions",
    validate_workflow=_validate_workflow,
)
