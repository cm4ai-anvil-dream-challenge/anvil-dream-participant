"""Generic helpers for participant WDL container image references."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import WDL


_DIGEST_IMAGE = re.compile(r"^[^\s/@]+(?:/[^\s/@]+)+@sha256:[0-9a-fA-F]{64}$")


class WorkflowContractError(ValueError):
    """A participant WDL or its submitted inputs violate a contract."""


@dataclass(frozen=True)
class ParticipantWorkflow:
    name: str
    tasks: tuple[Any, ...]


def is_literal(expr: Any) -> bool:
    if isinstance(expr, WDL.Expr.String):
        return len(expr.parts) == 3 and expr.parts[0] == '"' and expr.parts[2] == '"'
    return isinstance(expr, (WDL.Expr.Int, WDL.Expr.Float, WDL.Expr.Boolean))


def literal_value(expr: Any) -> str:
    if isinstance(expr, WDL.Expr.String):
        return str(expr.parts[1])
    if isinstance(expr, WDL.Expr.Boolean):
        return str(expr.value).lower()
    return str(expr.value)


def validate_container_image(image: str) -> str:
    """Require an immutable OCI image reference without assuming a registry."""
    if not isinstance(image, str) or not _DIGEST_IMAGE.fullmatch(image):
        raise WorkflowContractError("container image must be digest-pinned with @sha256:<digest>")
    return image


def validate_workflow_images(workflow: ParticipantWorkflow) -> tuple[str, ...]:
    """Return all validated container image references in task order."""
    images: list[str] = []
    for task in workflow.tasks:
        expression = (task.runtime or {}).get("docker")
        if expression is None or not is_literal(expression):
            raise WorkflowContractError(f"task {task.name} docker image must be a literal")
        images.append(validate_container_image(literal_value(expression)))
    return tuple(images)
