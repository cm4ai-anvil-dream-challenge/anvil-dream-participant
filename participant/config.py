"""Participant-owned configuration and example-value guard."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Required configuration is missing or malformed."""


PLACEHOLDER_PREFIXES = ("REPLACE_WITH_", "YOUR_", "YOU@")


def find_placeholders(value: object, path: str = "") -> tuple[tuple[str, object], ...]:
    """Recursively find unresolved example values in TOML-like structures."""
    issues: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            issues.extend(find_placeholders(item, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            issues.extend(find_placeholders(item, f"{path}[{index}]"))
    elif is_dataclass(value):
        for field in fields(value):
            child = f"{path}.{field.name}" if path else field.name
            issues.extend(find_placeholders(getattr(value, field.name), child))
    elif isinstance(value, Path):
        normalized = str(value).strip().upper()
        if normalized.startswith(PLACEHOLDER_PREFIXES):
            issues.append((path, str(value)))
    elif isinstance(value, str):
        normalized = value.strip().upper()
        if normalized.startswith(PLACEHOLDER_PREFIXES):
            issues.append((path, value))
    return tuple(issues)


def require_no_placeholders(
    value: object, *, allowed_paths: set[str] | frozenset[str] = frozenset()
) -> None:
    """Fail closed when a cloud command would otherwise use example values."""
    issues = tuple(issue for issue in find_placeholders(value) if issue[0] not in allowed_paths)
    if issues:
        details = ", ".join(f"{path}={value!r}" for path, value in issues)
        raise ConfigError(
            "configuration contains unresolved placeholders: "
            f"{details}. Copy the example TOML and replace every REPLACE_WITH_/YOUR_ value."
        )


PARTICIPANT_EVALUATION_ID = "9620108"
PARTICIPANT_QUEUE_NAME = "CM4AI-AnVIL DREAM Challenge Submissions"


DEFAULT_TERRA_SAM_URL = "https://sam.dsde-prod.broadinstitute.org"

ParticipantConfig = dict[str, dict[str, object]]


def participant_config_for_evaluation(
    evaluation_id: str = PARTICIPANT_EVALUATION_ID,
    *,
    queue_name: str | None = None,
) -> ParticipantConfig:
    """Route a participant submission to the specified Synapse Evaluation."""
    return {
        "synapse": {
            "queue_name": queue_name or (
                PARTICIPANT_QUEUE_NAME
                if evaluation_id == PARTICIPANT_EVALUATION_ID
                else f"Synapse Evaluation {evaluation_id}"
            ),
            "submission_parent_id": None,
            "evaluation_id": evaluation_id,
        },
        "terra": {"sam_url": DEFAULT_TERRA_SAM_URL},
    }
