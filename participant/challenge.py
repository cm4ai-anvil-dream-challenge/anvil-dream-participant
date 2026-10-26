"""Public challenge profile contract and explicit profile loading."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


class BenchmarkOperation(Protocol):
    def __call__(
        self,
        results: Path,
        *,
        dataset_id: str,
        comparison_report_path: Path,
        aggregate_path: Path,
        score_result_path: Path,
    ) -> object: ...


@dataclass(frozen=True)
class ChallengeProfile:
    name: str
    evaluation_id: str
    queue_name: str
    validate_workflow: Callable[[Path], None]
    benchmark: BenchmarkOperation | None = None


def load_challenge(reference: str) -> ChallengeProfile:
    """Import and validate one profile named as ``module:attribute``."""
    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ValueError("challenge must be specified as module:attribute")
    module_name, attribute = reference.split(":")
    if (
        not module_name
        or not attribute
        or not all(part.isidentifier() for part in module_name.split("."))
        or not attribute.isidentifier()
    ):
        raise ValueError("challenge must be specified as module:attribute")

    try:
        module = importlib.import_module(module_name)
    except (ImportError, ValueError) as error:
        raise ValueError(f"could not import challenge module {module_name!r}: {error}") from error
    try:
        profile = getattr(module, attribute)
    except AttributeError as error:
        raise ValueError(f"challenge profile {reference!r} does not exist") from error
    if not isinstance(profile, ChallengeProfile):
        raise ValueError(f"challenge profile {reference!r} must be a ChallengeProfile")
    if not isinstance(profile.name, str) or not profile.name.strip():
        raise ValueError("challenge profile name must not be empty")
    if (
        not isinstance(profile.evaluation_id, str)
        or not profile.evaluation_id.isdecimal()
        or int(profile.evaluation_id) <= 0
    ):
        raise ValueError("challenge profile evaluation_id must be a positive number")
    if not isinstance(profile.queue_name, str) or not profile.queue_name.strip():
        raise ValueError("challenge profile queue_name must not be empty")
    if not callable(profile.validate_workflow):
        raise ValueError("challenge profile validate_workflow must be callable")
    if profile.benchmark is not None and not callable(profile.benchmark):
        raise ValueError("challenge profile benchmark must be callable or None")
    return profile
