"""Participant-owned configuration and example-value guard."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Required configuration is missing or malformed."""


PLACEHOLDER_PREFIXES = ("REPLACE_WITH_", "YOUR_", "YOU@")


@dataclass(frozen=True)
class PlaceholderIssue:
    """One unresolved example value found at a configuration boundary."""

    path: str
    value: str


def find_placeholders(value: object, path: str = "") -> tuple[PlaceholderIssue, ...]:
    """Recursively find unresolved example values in TOML-like structures."""
    issues: list[PlaceholderIssue] = []
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
            issues.append(PlaceholderIssue(path, str(value)))
    elif isinstance(value, str):
        normalized = value.strip().upper()
        if normalized.startswith(PLACEHOLDER_PREFIXES):
            issues.append(PlaceholderIssue(path, value))
    return tuple(issues)


def require_no_placeholders(
    value: object, *, allowed_paths: set[str] | frozenset[str] = frozenset()
) -> None:
    """Fail closed when a cloud command would otherwise use example values."""
    issues = tuple(issue for issue in find_placeholders(value) if issue.path not in allowed_paths)
    if issues:
        details = ", ".join(f"{issue.path}={issue.value!r}" for issue in issues)
        raise ConfigError(
            "configuration contains unresolved placeholders: "
            f"{details}. Copy the example TOML and replace every REPLACE_WITH_/YOUR_ value."
        )


@dataclass(frozen=True)
class ParticipantSynapseConfig:
    queue_name: str
    submission_parent_id: str | None = None
    evaluation_id: str | None = None


@dataclass(frozen=True)
class ParticipantTerraConfig:
    rawls_url: str
    sam_url: str = "https://sam.dsde-prod.broadinstitute.org"


@dataclass(frozen=True)
class ParticipantConfig:
    synapse: ParticipantSynapseConfig
    terra: ParticipantTerraConfig


PARTICIPANT_QUEUE_NAME = "CM4AI AnVIL DREAM Challenge Evaluation"
PARTICIPANT_EVALUATION_ID = "9620030"
PARTICIPANT_RAWLS_URL = "https://rawls.dsde-prod.broadinstitute.org"
def participant_config_for_evaluation(
    evaluation_id: str = PARTICIPANT_EVALUATION_ID,
) -> ParticipantConfig:
    """Route a participant submission to the specified Synapse Evaluation."""
    queue_name = (
        PARTICIPANT_QUEUE_NAME
        if evaluation_id == PARTICIPANT_EVALUATION_ID
        else f"Synapse Evaluation {evaluation_id}"
    )
    return ParticipantConfig(
        synapse=ParticipantSynapseConfig(queue_name=queue_name, evaluation_id=evaluation_id),
        terra=ParticipantTerraConfig(rawls_url=PARTICIPANT_RAWLS_URL),
    )
