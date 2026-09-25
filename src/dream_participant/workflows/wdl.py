"""Model-neutral contract for a participant prediction task.

The organizer owns data selection, lane planning, comparison, and execution
policy. Participants own one task, its immutable container image, weights, and
the command that runs their model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import WDL


PARTICIPANT_TASK_NAME = "Predict"
PREDICTION_INPUT_STRUCT = "PredictionInput"
_DIGEST_IMAGE = re.compile(r"^[^\s/@]+(?:/[^\s/@]+)+@sha256:[0-9a-fA-F]{64}$")
_PARTICIPANT_RUNTIME_KEYS = frozenset({"docker"})


class WorkflowContractError(ValueError):
    """A participant WDL or its submitted inputs violate the contract."""


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


def _load_document(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowContractError(f"could not read WDL: {error}") from error
    if re.search(r"(?m)^\s*import\s+", text):
        raise WorkflowContractError("participant WDL imports are not supported")
    if "gs://" in text:
        raise WorkflowContractError(
            "hardcoded gs:// references are unsupported; participant data belongs in weights"
        )
    try:
        document = WDL.load(str(path))
    except Exception as error:
        raise WorkflowContractError(f"invalid WDL: {error}") from error
    if document.wdl_version != "1.0":
        raise WorkflowContractError("workflow must declare WDL version 1.0")
    return document


def _validate_participant_runtime(task: Any, *, require_digest_images: bool) -> None:
    """Accept only the participant-owned Docker image in a submitted task."""
    runtime = task.runtime or {}
    if not runtime:
        raise WorkflowContractError(f"task {task.name} must have a runtime block")
    if set(runtime) != _PARTICIPANT_RUNTIME_KEYS:
        missing = sorted(_PARTICIPANT_RUNTIME_KEYS - set(runtime))
        unsupported = sorted(set(runtime) - _PARTICIPANT_RUNTIME_KEYS)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unsupported:
            details.append(f"unsupported {', '.join(unsupported)}")
        raise WorkflowContractError(
            f"task {task.name} runtime keys must be exactly docker ({'; '.join(details)})"
        )
    docker = runtime.get("docker")
    if docker is None or not is_literal(docker):
        raise WorkflowContractError(f"task {task.name} docker image must be a literal")
    if require_digest_images:
        validate_container_image(literal_value(docker))


def _validate_participant_command(task: Any) -> None:
    """Require the organizer-owned manifest placeholder and known WDL values."""
    allowed_expressions = {"write_json(inputs)", "weights"}
    expressions = []
    for part in task.command.parts:
        if isinstance(part, str):
            continue
        expression = str(getattr(part, "expr", ""))
        expressions.append(expression)
        if expression not in allowed_expressions:
            raise WorkflowContractError(
                "participant task command interpolations may use only write_json(inputs) and weights"
            )
    if expressions.count("write_json(inputs)") != 1:
        raise WorkflowContractError(
            "participant task command must create one localized manifest with write_json(inputs)"
        )


def _validate_no_postinput_declarations(task: Any) -> None:
    """Keep input-derived files in command evaluation, after path localization."""
    if task.postinputs:
        raise WorkflowContractError("participant task must not have post-input declarations")


def _validate_prediction_input_struct(document: Any) -> None:
    """Require the one organizer-facing record shared by every prediction image."""
    names = {binding.name for binding in document.struct_typedefs}
    if names != {PREDICTION_INPUT_STRUCT}:
        raise WorkflowContractError(
            "participant WDL must declare exactly the PredictionInput struct"
        )
    struct = document.struct_typedefs[PREDICTION_INPUT_STRUCT]
    expected = {
        "image": WDL.Type.File,
        "target_protein": WDL.Type.String,
        "cell_line": WDL.Type.String,
    }
    if set(struct.members) != set(expected):
        raise WorkflowContractError(
            "PredictionInput must contain exactly image, target_protein, and cell_line"
        )
    for name, expected_type in expected.items():
        member = struct.members[name]
        if member.optional or not isinstance(member, expected_type):
            raise WorkflowContractError(
                f"PredictionInput.{name} must be a non-optional {expected_type.__name__}"
            )


def parse_participant_workflow(
    path: str | Path,
    *,
    max_wdl_bytes: int = 1_048_576,
    require_digest_images: bool = True,
    require_exact_participant_shape: bool = False,
) -> ParticipantWorkflow:
    """Parse the exact participant task interface supported by the organizer."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise WorkflowContractError(f"WDL file not found: {source}")
    if source.stat().st_size > max_wdl_bytes:
        raise WorkflowContractError(f"WDL exceeds the {max_wdl_bytes}-byte limit")
    document = _load_document(source)
    if document.workflow is not None:
        raise WorkflowContractError(
            "participant WDL must contain one task and no workflow; the organizer owns orchestration"
        )
    if len(document.tasks) != 1:
        raise WorkflowContractError("participant WDL must declare exactly one task")
    task = document.tasks[0]
    if task.name != PARTICIPANT_TASK_NAME:
        raise WorkflowContractError(f"participant task name must be {PARTICIPANT_TASK_NAME}")

    _validate_prediction_input_struct(document)
    expected = {"inputs": WDL.Type.Array, "weights": WDL.Type.File}
    declarations = {declaration.name: declaration for declaration in task.inputs}
    if set(declarations) != set(expected):
        missing = sorted(set(expected) - set(declarations))
        extra = sorted(set(declarations) - set(expected))
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unsupported {', '.join(extra)}")
        inputs_declaration = declarations.get("inputs")
        if inputs_declaration is not None and not isinstance(
            inputs_declaration.type, WDL.Type.Array
        ):
            details.append("input inputs must be a non-optional Array[PredictionInput]")
        raise WorkflowContractError(
            f"{PARTICIPANT_TASK_NAME} inputs must be exactly inputs and weights "
            f"({'; '.join(details)})"
        )
    for name, expected_type in expected.items():
        declaration = declarations[name]
        if name == "inputs":
            if (
                declaration.type.optional
                or not isinstance(declaration.type, WDL.Type.Array)
                or declaration.type.nonempty
                or declaration.type.item_type.optional
                or not isinstance(declaration.type.item_type, WDL.Type.StructInstance)
                or declaration.type.item_type.type_name != PREDICTION_INPUT_STRUCT
            ):
                raise WorkflowContractError(
                    "input inputs must be a non-optional Array[PredictionInput]"
                )
        elif declaration.type.optional or not isinstance(declaration.type, expected_type):
            raise WorkflowContractError(
                f"input {name} must be a non-optional {expected_type.__name__}"
            )
        if declaration.expr is not None:
            raise WorkflowContractError(f"input {name} must not define a default")

    outputs = {output.name: output for output in task.outputs}
    prediction = outputs.get("predictions")
    if set(outputs) != {"predictions"} or prediction is None:
        raise WorkflowContractError(
            f"{PARTICIPANT_TASK_NAME} must expose only the predictions output"
        )
    if (
        prediction.type.optional
        or not isinstance(prediction.type, WDL.Type.Array)
        or prediction.type.nonempty
    ):
        raise WorkflowContractError("predictions must be a non-optional Array[File]")
    if prediction.type.item_type.optional or not isinstance(
        prediction.type.item_type, WDL.Type.File
    ):
        raise WorkflowContractError("predictions must be a non-optional Array[File]")

    _validate_participant_runtime(task, require_digest_images=require_digest_images)
    if require_exact_participant_shape:
        _validate_no_postinput_declarations(task)
        _validate_participant_command(task)
    return ParticipantWorkflow(str(task.name), (task,))


def validate_workflow_images(workflow: ParticipantWorkflow) -> tuple[str, ...]:
    """Return all validated container image references in task order."""
    images: list[str] = []
    for task in workflow.tasks:
        expression = (task.runtime or {}).get("docker")
        if expression is None or not is_literal(expression):
            raise WorkflowContractError(f"task {task.name} docker image must be a literal")
        images.append(validate_container_image(literal_value(expression)))
    return tuple(images)
