"""Equal-weight benchmark score derived from evaluation-wide metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .aggregate import EvaluationAggregate
from .contract import EvaluationResultContractError, _canonical_bytes, _digest, _number, _text

SCORE_RESULT_SCHEMA = "cm4ai-score-result"
SCORE_COMPONENTS = (
    "mae_score",
    "rmse_score",
    "maximum_error_score",
    "cosine_similarity_score",
)


@dataclass(frozen=True)
class ScoreResult:
    dataset_id: str
    aggregate_sha256: str
    score: float
    components: dict[str, float]
    schema: str = SCORE_RESULT_SCHEMA
    higher_is_better: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "dataset_id": self.dataset_id,
            "aggregate_sha256": self.aggregate_sha256,
            "higher_is_better": self.higher_is_better,
            "components": {name: self.components[name] for name in SCORE_COMPONENTS},
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, value: object) -> ScoreResult:
        required = {
            "schema",
            "dataset_id",
            "aggregate_sha256",
            "higher_is_better",
            "components",
            "score",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise EvaluationResultContractError("score result has an unexpected schema")
        if value["schema"] != SCORE_RESULT_SCHEMA:
            raise EvaluationResultContractError(f"schema must be {SCORE_RESULT_SCHEMA}")
        if value["higher_is_better"] is not True:
            raise EvaluationResultContractError("higher_is_better must be true")
        raw_components = value["components"]
        if not isinstance(raw_components, dict) or set(raw_components) != set(SCORE_COMPONENTS):
            raise EvaluationResultContractError("score components have an unexpected schema")
        components = {
            name: _number(raw_components[name], f"components.{name}") for name in SCORE_COMPONENTS
        }
        if any(not 0.0 <= component <= 1.0 for component in components.values()):
            raise EvaluationResultContractError("score components must be between 0 and 1")
        score = _number(value["score"], "score")
        if not 0.0 <= score <= 1.0:
            raise EvaluationResultContractError("score must be between 0 and 1")
        expected_score = math.fsum(components.values()) / len(SCORE_COMPONENTS)
        if score != expected_score:
            raise EvaluationResultContractError("score does not match the equal-weight component mean")
        return cls(
            dataset_id=_text(value["dataset_id"], "dataset_id"),
            aggregate_sha256=_digest(value["aggregate_sha256"], "aggregate_sha256"),
            score=score,
            components=components,
        )


def _error_component(error: float, target_rms: float) -> float:
    if target_rms == 0.0:
        return 1.0 if error == 0.0 else 0.0
    return target_rms / (target_rms + error)


def score_evaluation(aggregate: EvaluationAggregate) -> ScoreResult:
    """Score global MAE, RMSE, max error, and cosine similarity equally."""
    aggregate = EvaluationAggregate.from_dict(aggregate.as_dict())
    target_rms = math.sqrt(
        aggregate.metrics.target_square_sum / aggregate.element_count
    )
    metrics = aggregate.metrics
    if metrics.cosine_similarity is None:
        cosine_component = (
            1.0
            if aggregate.metrics.prediction_square_sum == 0.0
            and aggregate.metrics.target_square_sum == 0.0
            else 0.0
        )
    else:
        cosine_component = (1.0 + metrics.cosine_similarity) / 2.0
    components = {
        "mae_score": _error_component(metrics.mean_absolute_error, target_rms),
        "rmse_score": _error_component(
            metrics.root_mean_squared_error, target_rms
        ),
        "maximum_error_score": _error_component(
            metrics.maximum_absolute_error, target_rms
        ),
        "cosine_similarity_score": cosine_component,
    }
    result = ScoreResult(
        dataset_id=aggregate.dataset_id,
        aggregate_sha256=aggregate.canonical_sha256(),
        score=math.fsum(components.values()) / len(SCORE_COMPONENTS),
        components=components,
    )
    return ScoreResult.from_dict(result.as_dict())


def write_score_result(result: ScoreResult, path: str | Path) -> Path:
    """Write one canonical score result JSON object."""
    parsed = ScoreResult.from_dict(result.as_dict())
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(_canonical_bytes(parsed.as_dict()))
    temporary.replace(destination)
    return destination
