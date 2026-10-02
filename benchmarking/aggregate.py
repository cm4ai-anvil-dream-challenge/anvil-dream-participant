"""Evaluation-wide measurements produced by the benchmark."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .contract import (
    EvaluationResultContractError,
    _canonical_bytes,
    _nonnegative_number,
    _number,
    _positive_int,
    _text,
)
from .metrics import ComparisonMetrics, _is_within_squared_error_bound

EVALUATION_AGGREGATE_SCHEMA = "cm4ai-evaluation-aggregate"
SUM_FIELDS = (
    "absolute_error_sum",
    "squared_error_sum",
    "dot_product_sum",
    "prediction_square_sum",
    "target_square_sum",
    "maximum_absolute_error",
)


@dataclass(frozen=True)
class EvaluationAggregate:
    dataset_id: str
    record_count: int
    element_count: int
    metrics: ComparisonMetrics
    schema: str = EVALUATION_AGGREGATE_SCHEMA

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "dataset_id": self.dataset_id,
            "record_count": self.record_count,
            "element_count": self.element_count,
            "metrics": self.metrics.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> EvaluationAggregate:
        required = {"schema", "dataset_id", "record_count", "element_count", "metrics"}
        if not isinstance(value, dict) or set(value) != required:
            raise EvaluationResultContractError("evaluation aggregate has an unexpected schema")
        if value["schema"] != EVALUATION_AGGREGATE_SCHEMA:
            raise EvaluationResultContractError(f"schema must be {EVALUATION_AGGREGATE_SCHEMA}")
        record_count = _positive_int(value["record_count"], "record_count")
        element_count = _positive_int(value["element_count"], "element_count")
        if element_count < record_count:
            raise EvaluationResultContractError("element_count cannot be less than record_count")
        raw = value["metrics"]
        if not isinstance(raw, dict) or set(raw) != set(ComparisonMetrics.__dataclass_fields__):
            raise EvaluationResultContractError("metrics has an unexpected schema")
        sums = {
            name: _number(raw[name], name) if name == "dot_product_sum"
            else _nonnegative_number(raw[name], name)
            for name in SUM_FIELDS
        }
        maximum = sums["maximum_absolute_error"]
        if maximum > sums["absolute_error_sum"]:
            raise EvaluationResultContractError(
                "maximum_absolute_error exceeds absolute_error_sum"
            )
        if not _is_within_squared_error_bound(
            sums["squared_error_sum"], maximum, element_count
        ):
            raise EvaluationResultContractError(
                "squared_error_sum exceeds element_count times maximum_absolute_error squared"
            )
        cosine = raw["cosine_similarity"]
        if cosine is not None:
            cosine = _number(cosine, "cosine_similarity")
            if not -1.000000000001 <= cosine <= 1.000000000001:
                raise EvaluationResultContractError("cosine_similarity must be between -1 and 1")
        metrics = ComparisonMetrics(
            **sums,
            mean_absolute_error=_nonnegative_number(raw["mean_absolute_error"], "mean_absolute_error"),
            root_mean_squared_error=_nonnegative_number(
                raw["root_mean_squared_error"], "root_mean_squared_error"
            ),
            cosine_similarity=cosine,
        )
        if metrics != ComparisonMetrics.from_sums(**sums, element_count=element_count):
            raise EvaluationResultContractError("metrics do not match additive sums")
        return cls(
            dataset_id=_text(value["dataset_id"], "dataset_id"),
            record_count=record_count,
            element_count=element_count,
            metrics=metrics,
        )

    def canonical_sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.as_dict())).hexdigest()


def write_evaluation_aggregate(aggregate: EvaluationAggregate, path: str | Path) -> Path:
    """Write one canonical, validated aggregate JSON object."""
    parsed = EvaluationAggregate.from_dict(aggregate.as_dict())
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(_canonical_bytes(parsed.as_dict()))
    temporary.replace(destination)
    return destination
