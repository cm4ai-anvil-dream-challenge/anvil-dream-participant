"""Participant-safe projection of a Synapse submission status."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParticipantSubmissionStatus:
    submission_id: str
    status: str | None
    dream_state: str | None
    comparison_status: str | None
    comparison_shard_count: int | None
    error: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "submission_id": self.submission_id,
            "status": self.status,
            "dream_state": self.dream_state,
            "comparison_status": self.comparison_status,
            "comparison_shard_count": self.comparison_shard_count,
            "error": self.error,
        }

    @classmethod
    def from_status(cls, submission_id: str, status: object) -> "ParticipantSubmissionStatus":
        if isinstance(status, dict):
            synapse_status = status.get("status")
            annotations = status.get("submissionAnnotations") or {}
        else:
            synapse_status = getattr(status, "status", None)
            annotations = getattr(status, "submissionAnnotations", {}) or {}
        if not isinstance(annotations, dict):
            annotations = {}
        raw_count = annotations.get("comparison_shard_count")
        try:
            count = int(raw_count) if raw_count is not None else None
        except (TypeError, ValueError):
            count = None
        return cls(
            str(submission_id),
            str(synapse_status) if synapse_status is not None else None,
            _text(annotations.get("dream_state")),
            _text(annotations.get("comparison_status")),
            count,
            _text(annotations.get("error")),
        )


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
