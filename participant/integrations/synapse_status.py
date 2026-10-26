"""Participant-safe Synapse submission status lookup."""

from __future__ import annotations

from typing import Any

from anvil_dream_participant.submissions.status import ParticipantSubmissionStatus


def read_submission_status(client: Any, submission_id: str) -> ParticipantSubmissionStatus:
    identifier = str(submission_id).strip()
    if not identifier:
        raise ValueError("submission_id cannot be empty")
    status = client.getSubmissionStatus(identifier)
    return ParticipantSubmissionStatus.from_status(identifier, status)
